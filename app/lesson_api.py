"""Owned lesson checkpoints around the existing exercise evidence loop."""
from hashlib import sha256
import asyncio
import re
from uuid import uuid4
from fastapi import HTTPException, UploadFile, Form
from fastapi.responses import Response
from pydantic import Field, ValidationError
from app.accent_practice import offer, resolve, discover, discovery_response, expire, offer_after_preference
from app.accents import acknowledgment as accent_acknowledgment
from app.accent_intent import interpret
from app.audio import validate_upload
from app.lesson import Lesson, CheckinReply, social_response, routine_checkin_reply, compose_checkin_reply, now, elapsed, closing_due, compose_closing, heard, prompt, introduction, safe_label, TARGET_SECONDS
from app.models import StrictModel, Transcription
from app.onboarding import ProfileExtraction
from app.recognition import reliable_recognition
from app.diagnostics import report_failure, operation, timed_lock
from app.services.tutor_tts import tutor_cache_signature, sanitize_tutor_audio

ACKNOWLEDGMENT = 'Okay, I heard you.'
ACKNOWLEDGMENTS = {'assessment': ACKNOWLEDGMENT, 'followup': 'Okay, I’ve heard that.',
                  'reflection': 'Let me keep that in mind.'}

class AcknowledgmentCue(StrictModel):
    cue: str = Field(pattern='^(assessment|followup|reflection)$', default='assessment')

class LessonRevision(StrictModel):
    revision: int = Field(ge=0)

def register_lesson_routes(app, repo, ai, lock, settings, prepare_exercise, public_exercise, passage_limits):
    acknowledgment_lock = asyncio.Lock()
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
            saved = repo.save_lesson(lesson, revision, interests, accent_preferences)
            if saved.status == 'completed' and settings.planner_enabled:
                repo.optional_roadmap()
            return saved
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
        name = sha256((str(repo.learner_id) + ':' + tutor_cache_signature(settings) + ':' + key + ':' + text).encode()).hexdigest() + '.' + kind
        data = repo.audio_blob(name)
        if data is None:
            with operation(settings, 'lesson.tts_provider'):
                generated = await ai().speak(text)
            with operation(settings, 'lesson.tts_sanitization'):
                data = await sanitize_tutor_audio(generated, kind, ai())
            with operation(settings, 'lesson.audio_persistence'):
                repo.cache_lesson_audio(name, data)
        return Response(data, media_type='audio/wav' if kind == 'wav' else 'audio/mpeg')

    from app.planner import preferred_topic
    from app.planner_api import register_planner_routes
    register_planner_routes(app, repo, ai, lock, settings, speech)

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
            roadmap = repo.optional_roadmap() if settings.planner_enabled else None
            planned = roadmap.upcoming[0] if roadmap else None
            at = now()
            lesson = Lesson(id=str(uuid4()), started_at=at, active_since=at,
                name=profile.name, focus=planned.focus if planned else profile.focus,
                plan_topic=planned.topic if planned else None, plan_number=planned.number if planned else None,
                previous=repo.previous_lesson())
            available_voices = await passage_limits.voices()
            history = repo.accent_lesson_history()
            lesson.accent_discovery = discover(profile.accent_preferences, history, available_voices)
            if not lesson.accent_discovery:
                lesson.accent_offered = offer(profile.accent_preferences, history, available_voices)
            try:
                repo.create_lesson(lesson, remember_only=not bool(available_voices))
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
            expire(lesson)
            lesson.accent_topic_review_pending = False
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
        async with timed_lock(settings, acknowledgment_lock, 'acknowledgment.audio'):
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
            # Existing lessons predate discovery scheduling at /start. Use the
            # next conversational boundary, preserving the topic answer and progress.
            if (lesson.phase in ('WELCOME_ACK', 'REVIEW_ACK')
                    and not lesson.accent_discovery and not lesson.accent_offered
                    and not lesson.accent_confirmation and not lesson.accent_resolved
                    and discover(repo.profile().accent_preferences,
                                 [item for item in repo.accent_lesson_history() if item.id != lesson.id],
                                 await passage_limits.voices())):
                updated.accent_discovery = True
                updated.phase = 'REVIEW'
                updated.accent_topic_review_pending = lesson.phase == 'WELCOME_ACK' and bool(lesson.previous)
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
            available_voices = await passage_limits.voices()
            discovering = (lesson.accent_discovery and not lesson.accent_discovery_resolved
                           and not lesson.accent_confirmation)
            context = (lesson.accent_consent.accent if lesson.accent_confirmation
                       else None if lesson.accent_resolved else lesson.accent_offered)
            parsed = interpret(text, offered=context, discovering=discovering,
                               practice_question=lesson.accent_confirmation)
            if not available_voices and parsed.kind in ('topic', 'preference') and not (lesson.accent_offered or lesson.accent_discovery or lesson.accent_confirmation):
                expire(lesson)
                accent_preferences = parsed.preferences
                interests = None
                if lesson.phase == 'REVIEW':
                    if accent_preferences is None or parsed.topic_text is not None:
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
                    if interest and lesson.plan_number is not None:
                        lesson.plan_topic = preferred_topic(interest, lesson.plan_topic)
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
            interests = None
            accent_preferences = parsed.preferences
            if parsed.topic_text:
                try:
                    with operation(settings, 'lesson.profile_extraction'):
                        extracted = ProfileExtraction.model_validate(await ai().extract_profile('interests', parsed.topic_text))
                    interests = extracted.interests
                    lesson.reflection = parsed.topic_text
                except Exception as exc:
                    check(lesson_id, body.revision)
                    if parsed.kind == 'topic':
                        raise
                    report_failure(settings, 'Lesson topic preference', exc)
            if discovering:
                lesson.accent_discovery_resolved = True
            if parsed.kind == 'preference':
                # Disclosure is interest, never acceptance of a pending offer.
                accent_reply = ('Got it! I’ll remember that accent preference.'
                                if any(accent in available_voices for accent in accent_preferences.accents) else
                                'I’ll remember that accent preference, though it isn’t available for practice yet.')
                if lesson.accent_consent.state != 'accepted' and lesson.accent_offered:
                    # A new disclosure is not acceptance and must not restart a
                    # question already offered/declined in this lesson.
                    expire(lesson)
                    lesson.accent_resolved = True
                if not available_voices:
                    accent_reply = accent_acknowledgment(accent_preferences)
            elif discovering and parsed.kind == 'topic':
                _, accent_reply = discovery_response(parsed)
                lesson.accent_resolved = True
            else:
                accent_reply = resolve(lesson, parsed, available_voices)
            if lesson.accent_confirmation and lesson.accent_resolved and lesson.accent_consent.state == 'none':
                # Stay at a response-taking checkpoint so the existing voice
                # loop speaks one clarification and records its answer.
                if lesson.phase == 'WELCOME':
                    lesson.accent_topic_review_pending = bool(lesson.previous)
                lesson.phase = 'REVIEW'
                lesson.pending = None
                return public(save(lesson, body.revision, interests, accent_preferences))
            if accent_reply and lesson.accent_consent.state == 'accepted':
                current = repo.current_exercise()
                if current and not repo.completed(current['id']):
                    accent_reply += ' We’ll finish your saved recording first.'
            if offer_after_preference(lesson, accent_preferences, available_voices):
                if lesson.phase == 'REVIEW':
                    # Ask immediately in the existing response-taking phase. Keep
                    # the normal topic review after this optional practice question.
                    if not (lesson.accent_discovery_resolved and lesson.reflection is not None):
                        lesson.accent_topic_review_pending = True
                    lesson.pending = None
                    return public(save(lesson, body.revision, interests, accent_preferences))
                accent_reply = 'Got it! I’ll remember that accent preference.'
            if accent_reply:
                if lesson.phase == 'REVIEW':
                    lesson.review_response = accent_reply
                    lesson.phase = 'REVIEW_ACK'
                else:
                    lesson.checkin = text
                    lesson.welcome_response = accent_reply
                    lesson.phase = 'WELCOME_ACK'
            elif lesson.phase == 'REVIEW':
                try:
                    with operation(settings, 'lesson.profile_extraction'):
                        if not parsed.topic_text:
                            extracted = ProfileExtraction.model_validate(await ai().extract_profile('interests', text))
                except ValidationError:
                    check(lesson_id, body.revision)
                    raise HTTPException(422, 'I couldn’t pick out a topic preference. Choose Retry conversation; '
                                        'your lesson and saved preferences are unchanged.') from None
                interests = extracted.interests  # Never replace goals, identity, scores or difficulty.
                lesson.reflection = text
                interest = next((safe_label(item) for item in interests or () if safe_label(item)), None)
                if interest and lesson.plan_number is not None:
                    lesson.plan_topic = preferred_topic(interest, lesson.plan_topic)
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
