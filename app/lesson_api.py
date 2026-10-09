"""Owned lesson checkpoints around the existing exercise evidence loop."""
from hashlib import sha256
from uuid import uuid4
import re
from fastapi import HTTPException, UploadFile, Form
from fastapi.responses import Response
from pydantic import Field, ValidationError
from app.audio import validate_upload, sanitize_generated_audio
from app.lesson import Lesson, CheckinReply, social_response, routine_checkin_reply, compose_checkin_reply, now, elapsed, closing_due, compose_closing, heard, prompt, introduction, safe_label, TARGET_SECONDS
from app.models import StrictModel, Transcription
from app.onboarding import ProfileExtraction
from app.accents import lesson_preference, only_accent_request, acknowledgment as accent_acknowledgment
from app.recognition import reliable_recognition
from app.diagnostics import report_failure, operation, timed_lock

ACKNOWLEDGMENT = 'Okay, I heard you.'
ACKNOWLEDGMENTS = {'assessment': ACKNOWLEDGMENT, 'followup': 'Okay, I’ve heard that.',
                  'reflection': 'Let me keep that in mind.'}

class AcknowledgmentCue(StrictModel):
    cue: str = Field(pattern='^(assessment|followup|reflection)$', default='assessment')

class LessonRevision(StrictModel):
    revision: int = Field(ge=0)

def register_lesson_routes(app, repo, ai, lock, settings, prepare_exercise, public_exercise):
    def owned(lesson_id):
        lesson = repo.lesson(lesson_id)
        if lesson is None:
            raise HTTPException(404, 'Lesson not found.')
        return lesson

    def check(lesson_id, revision):
        lesson = owned(lesson_id)
        if lesson.status != 'active' or lesson.revision != revision:
            raise HTTPException(409, 'Reload your current lesson checkpoint.')
        return lesson

    def save(lesson, revision, interests=None, accent_preferences=None):
        try:
            return repo.save_lesson(lesson, revision, interests, accent_preferences)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def public(lesson):
        at = now()
        intro_pending = bool(lesson.phase == 'EXERCISE' and lesson.exercise_id
            and lesson.introduced_exercise_id != lesson.exercise_id
            and (not repo.conversation(lesson.exercise_id) or repo.conversation(lesson.exercise_id).state == 'LISTENING')
            and not repo.completed(lesson.exercise_id))
        return {'id': lesson.id, 'revision': lesson.revision, 'status': lesson.status,
            'phase': lesson.phase, 'prompt': prompt(lesson), 'pending': lesson.pending,
            'exercise': public_exercise(repo.exercise(lesson.exercise_id)) if lesson.exercise_id else None,
            'introduction_pending': intro_pending,
            'introduction_prompt': introduction(lesson) if intro_pending else None,
            'elapsed_seconds': elapsed(lesson, at), 'remaining_seconds': max(0, TARGET_SECONDS - elapsed(lesson, at)),
            'server_time': at, 'focus': lesson.focus, 'exercises_completed': lesson.exercises_completed,
            'strength': lesson.strength, 'improvement_focus': lesson.improvement_focus,
            'encouragement': f'Thanks for making time to listen, {lesson.name}.'}

    def close(lesson):
        work = repo.lesson_work(lesson.id)
        compose_closing(lesson, [item['evaluation'] for item in work], [item['content']['topic'] for item in work],
                        [item['content'] for item in work])
        lesson.phase = 'CLOSING'
        lesson.exercise_id = None

    async def speech(text, key):
        kind = 'wav' if settings.provider == 'demo' else 'mp3'
        # Cache is scoped to the verified owner and complete spoken message.
        name = sha256((str(repo.learner_id) + ':' + key + ':' + text).encode()).hexdigest() + '.' + kind
        data = repo.audio_blob(name)
        if data is None:
            with operation(settings, 'lesson.tts_provider'):
                generated = await ai().speak(text)
            with operation(settings, 'lesson.tts_sanitization'):
                data = await sanitize_generated_audio(generated, kind)
            with operation(settings, 'lesson.audio_persistence'):
                repo.cache_lesson_audio(name, data)
        return Response(data, media_type='audio/wav' if kind == 'wav' else 'audio/mpeg')

    @app.get('/api/lessons/current')
    async def current():
        lesson = repo.lesson()
        return public(lesson) if lesson else None

    @app.get('/api/lessons/{lesson_id}')
    async def get_lesson(lesson_id: str):
        return public(owned(lesson_id))

    @app.post('/api/lessons/start')
    async def start():
        async with lock:
            existing = repo.lesson()
            if existing:
                return public(existing)
            profile = repo.profile()
            if profile.onboarding_status != 'complete':
                raise HTTPException(409, 'Complete your spoken onboarding first.')
            at = now()
            lesson = Lesson(id=str(uuid4()), started_at=at, active_since=at,
                name=profile.name, focus=profile.focus, previous=repo.previous_lesson())
            try:
                repo.create_lesson(lesson)
            except ValueError:
                return public(repo.lesson())
            return public(lesson)

    @app.post('/api/lessons/{lesson_id}/resume')
    async def resume(lesson_id: str):
        async with lock:
            lesson = owned(lesson_id)
            if lesson.status == 'paused':
                revision = lesson.revision
                lesson.status = 'active'
                lesson.active_since = now()
                save(lesson, revision)
            return public(lesson)

    @app.post('/api/lessons/{lesson_id}/end')
    async def end(lesson_id: str):
        # Do not wait for a provider lock: invalidate delayed checkpoint writes now.
        lesson = owned(lesson_id)
        if lesson.status == 'active':
            revision = lesson.revision
            lesson.elapsed_seconds = elapsed(lesson, now())
            lesson.active_since = None
            lesson.status = 'paused'
            save(lesson, revision)
        return public(lesson)

    @app.post('/api/lessons/{lesson_id}/audio')
    async def audio(lesson_id: str, body: LessonRevision):
        lesson = check(lesson_id, body.revision)
        text = prompt(lesson)
        if text is None:
            raise HTTPException(409, 'This phase has no lesson speech.')
        response = await speech(text, 'lesson-v1')
        check(lesson_id, body.revision)  # A delayed body cannot continue an ended phase.
        return response

    @app.post('/api/recognition/acknowledgment-audio')
    async def acknowledgment(body: AcknowledgmentCue = AcknowledgmentCue()):
        # Neutral, cached, no LLM generation or lesson progress mutation.
        return await speech(ACKNOWLEDGMENTS[body.cue], 'acknowledgment-v2')

    @app.post('/api/lessons/{lesson_id}/introduction-audio')
    async def introduction_audio(lesson_id: str, body: LessonRevision):
        async with lock:
            lesson = check(lesson_id, body.revision)
            if not public(lesson)['introduction_pending']:
                raise HTTPException(409, 'Continue from your current exercise checkpoint.')
            text = introduction(lesson)
        try:
            response = await speech(text, 'exercise-introduction-v1')
        except Exception as exc:
            check(lesson_id, body.revision)
            report_failure(settings, 'Lesson introduction speech', exc)
            raise HTTPException(503, 'I couldn’t get that introduction ready. Choose Retry conversation; your listening progress is saved.') from None
        check(lesson_id, body.revision)
        return response

    @app.post('/api/lessons/{lesson_id}/introduction-heard')
    async def introduction_heard(lesson_id: str, body: LessonRevision):
        async with lock:
            lesson = check(lesson_id, body.revision)
            if not public(lesson)['introduction_pending']:
                raise HTTPException(409, 'Continue from your current exercise checkpoint.')
            lesson.introduced_exercise_id = lesson.exercise_id
            return public(save(lesson, body.revision))

    @app.post('/api/lessons/{lesson_id}/heard')
    async def finished_speech(lesson_id: str, body: LessonRevision):
        async with lock:
            lesson = check(lesson_id, body.revision)
            try:
                updated = heard(lesson, now())
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return public(save(updated, body.revision))

    @app.post('/api/lessons/{lesson_id}/attempts')
    async def attempt(lesson_id: str, audio: UploadFile, revision: int = Form(ge=0)):
        try:
            async with timed_lock(settings, lock, 'lesson.upload'):
                lesson = check(lesson_id, revision)
                if lesson.phase not in ('WELCOME', 'REVIEW'):
                    raise HTTPException(409, 'This phase does not request a personal response.')
                with operation(settings, 'lesson.upload_validation'):
                    data, filename = await validate_upload(audio, settings)
                with operation(settings, 'lesson.transcription'):
                    lesson.pending = Transcription.model_validate(await ai().transcribe(data, filename))
                with operation(settings, 'lesson.pending_persistence'):
                    return public(save(lesson, revision))
        finally:
            await audio.close()

    @app.post('/api/lessons/{lesson_id}/answer')
    async def answer(lesson_id: str, body: LessonRevision):
        async with timed_lock(settings, lock, 'lesson.answer'):
            lesson = check(lesson_id, body.revision)
            if lesson.phase not in ('WELCOME', 'REVIEW') or lesson.pending is None:
                raise HTTPException(409, 'Record a response to the current question first.')
            if not reliable_recognition(lesson.pending, settings.min_transcription_confidence):
                raise HTTPException(422, 'Recognition is uncertain. Please record again.')
            text = lesson.pending.text
            accent_preferences = lesson_preference(text, lesson.suggested_accent if lesson.phase == 'REVIEW' else None)
            interests = None
            if lesson.phase == 'REVIEW':
                if accent_preferences is None or not only_accent_request(text):
                    try:
                        with operation(settings, 'lesson.profile_extraction'):
                            extracted = ProfileExtraction.model_validate(await ai().extract_profile('interests', text))
                        interests = extracted.interests
                    except Exception as exc:
                        check(lesson_id, body.revision)
                        if accent_preferences is None:
                            if isinstance(exc, ValidationError):
                                raise HTTPException(422, 'I couldn’t pick out a topic preference. Choose Retry conversation; '
                                                    'your lesson and saved preferences are unchanged.') from None
                            raise
                        # An optional accent request never gates lesson availability.
                        report_failure(settings, 'Lesson topic preference', exc)
                    if accent_preferences is not None and interests == [] and not re.search(
                            r'\bno (?:topic|interest) preference\b', text, re.I):
                        interests = None
                lesson.reflection = text
                interest = next((safe_label(item) for item in interests or () if safe_label(item)), None)
                lesson.review_response = (f'I’ll keep {interest} in mind as we choose our listening topics.'
                                          if interest else 'We can continue with today’s listening.')
                lesson.phase = 'REVIEW_ACK'
            else:
                lesson.checkin = text
                try:
                    reply = routine_checkin_reply(text)
                    if reply is None:
                        with operation(settings, 'lesson.response_generation'):
                            reply = CheckinReply.model_validate(await ai().respond_checkin(text))
                    with operation(settings, 'lesson.checkin_composition'):
                        lesson.welcome_response = compose_checkin_reply(text, reply)
                except Exception as exc:
                    report_failure(settings, 'Lesson check-in response', exc)
                    lesson.welcome_response = social_response(text)
                lesson.phase = 'WELCOME_ACK'
            if accent_preferences is not None:
                if lesson.phase == 'WELCOME_ACK':
                    lesson.suggested_accent = None
                # Application-owned reply cannot promise an unsupported voice,
                # even if generated social text would have done so.
                if lesson.phase == 'REVIEW_ACK':
                    lesson.review_response = accent_acknowledgment(accent_preferences) + (
                        f' I’ll also keep {interest} in mind for our topics.' if interest else '')
                else:
                    lesson.welcome_response = accent_acknowledgment(accent_preferences)
            lesson.pending = None
            with operation(settings, 'lesson.answer_persistence'):
                return public(save(lesson, body.revision, interests, accent_preferences))

    @app.post('/api/lessons/{lesson_id}/exercise')
    async def exercise(lesson_id: str, body: LessonRevision):
        async with lock:
            lesson = check(lesson_id, body.revision)
            if lesson.phase != 'EXERCISE':
                raise HTTPException(409, 'Exercises are unavailable in this lesson phase.')
            if lesson.exercise_id:
                return public(lesson)
            if closing_due(lesson, now()):
                close(lesson)
                return public(save(lesson, body.revision))
            try:
                await prepare_exercise(lesson)
            except HTTPException:
                raise
            except Exception as exc:
                latest = check(lesson_id, body.revision)
                report_failure(settings, 'Lesson exercise generation', exc)
                if closing_due(latest, now()):
                    close(latest)
                    return public(save(latest, body.revision))
                raise HTTPException(503, 'I couldn’t get the next listening passage ready. '
                                    'Choose Retry conversation to continue; your lesson progress is saved.') from None
            return public(owned(lesson_id))

    @app.post('/api/lessons/{lesson_id}/advance')
    async def advance(lesson_id: str, body: LessonRevision):
        async with lock:
            lesson = check(lesson_id, body.revision)
            if lesson.phase != 'EXERCISE' or not lesson.exercise_id or not repo.completed(lesson.exercise_id):
                raise HTTPException(409, 'Finish the active exercise first.')
            conversation = repo.conversation(lesson.exercise_id)
            if conversation and conversation.state != 'READY_FOR_NEXT':
                raise HTTPException(409, 'Finish the spoken coaching first.')
            lesson.exercises_completed = len(repo.lesson_work(lesson.id))
            lesson.exercise_id = None
            if closing_due(lesson, now()):
                close(lesson)
            return public(save(lesson, body.revision))
