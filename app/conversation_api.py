"""Turn-based coaching routes alongside the unchanged V0.1 evaluation endpoint."""
import json
import asyncio
from hashlib import sha256
from uuid import uuid4
from types import SimpleNamespace
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import Field, model_validator
from app.services.tutor_tts import TutorSpeech, tutor_cue_cache_id
from app.conversation import validate_evidence, choose_followup, final_evaluation, adapt_final
from app.feedback import final_feedback
from app.lesson import public_topic, evidence_gaps, evidence_tip
from app.models import EvidenceEvaluation, Evaluation
from app.diagnostics import operation, timed_lock
from app.models import StrictModel, Conversation, ConversationTurn, ExerciseContent
from app.models import Transcription
from app.recognition import reliable_recognition
from app.lesson_limit import LimitRefused
from app.tutor_voice import tutor_settings

SUMMARY_PROMPT = 'Tell me what you understood. Use your own words; grammar does not matter.'

class TurnConfirmation(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    confirmed: bool
    followup_id: str | None = None
    hands_free: bool = False

class CueRequest(StrictModel):
    cue_id: str = Field(min_length=1, max_length=80)
    lesson_id: str | None = Field(default=None, min_length=1, max_length=80)
    lesson_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def paired_checkpoint(self):
        if (self.lesson_id is None) != (self.lesson_revision is None):
            raise ValueError('Provide the lesson and revision together.')
        return self


def register_conversation_routes(app, repo, ai, lock, settings, audio_dir, exercise_or_404, restore_audio, new_lock):
    retry_lock = new_lock()
    offload = app.state.offload   # AUD-87: database sections run in worker threads when AUDLI_DB_OFFLOAD is on; inline otherwise
    @app.post('/api/recognition/retry-audio')
    async def retry_audio():
        # Protected by the same authenticated request boundary as every /api resource.
        tutor = TutorSpeech(settings, ai(), repo.profile().tutor_voice, 'recovery')
        kind = tutor.kind
        text = "I didn't quite catch that. Could you say it again?"
        name = sha256((str(repo.learner_id) + ':retry-v1:' + tutor.signature + ':' + text).encode()).hexdigest() + '.' + kind
        async with timed_lock(settings, retry_lock, 'retry.audio'):
            data = repo.audio_blob(name)
            if data is None:
                app.state.limits.admit('practice', 'retry_audio')  # Cache misses only; replay never generates.
                data = await tutor.sanitize(await tutor.speak(text))
                if not tutor.fallback:
                    repo.cache_lesson_audio(name, data)
        return Response(data, media_type='audio/wav' if kind == 'wav' else 'audio/mpeg', headers=tutor.headers)

    def audio_cache_id(cue_id, text):
        # Old evaluator-report audio must never replay as terminal feedback.
        return tutor_cue_cache_id(tutor_settings(settings, repo.profile().tutor_voice), cue_id, text)

    def previous_advice(exercise_id):
        lesson = repo.lesson_for_exercise(exercise_id)
        if not lesson:
            return []
        tips = []
        for item in repo.lesson_work(lesson.id):
            if item['exercise_id'] == exercise_id:
                break
            tips.extend(evidence_tip(*gap) for gap in evidence_gaps(item['evaluation'], item['content']))
        return tips

    def cue(exercise_id, cue_id):
        conversation = repo.conversation(exercise_id)
        completed = repo.completed(exercise_id)
        if cue_id == 'feedback':
            if not completed:
                raise HTTPException(403, 'Complete assessment before feedback.')
            result = repo.result(completed)
            model = EvidenceEvaluation if result['evaluation'].get('version') == 'v0.2' else Evaluation
            evaluation = model.model_validate(result['evaluation'])
            content = ExerciseContent.model_validate_json(exercise_or_404(exercise_id)['content'])
            return final_feedback(evaluation, SimpleNamespace(**result['adaptation']), settings.max_feedback_words, content,
                                  previous_advice(exercise_id))
        if not conversation:
            raise HTTPException(409, 'Listen to the clip first.')
        if cue_id == 'summary':
            return SUMMARY_PROMPT
        question = next((question for question in conversation.followups if question.id == cue_id), None)
        if question:
            return "There's one thing I'd like to check. " + question.question
        raise HTTPException(404, 'Coaching prompt unavailable.')

    def public_conversation(exercise_id):
        conversation = repo.conversation(exercise_id)
        completed = repo.completed(exercise_id)
        result = repo.result(completed) if completed else None
        state = conversation.state if conversation else ('READY_FOR_NEXT' if completed else 'LISTENING')
        cue_id = ('feedback' if result else conversation.active_followup.id
                  if conversation and conversation.active_followup else 'summary' if conversation else None)
        pending = None
        if conversation and not completed:
            row = repo.attempt(conversation.pending_attempt_id) if conversation.pending_attempt_id else None
            if row:
                pending = {'id': row['id'], 'transcription': json.loads(row['transcription']),
                           'confirmed_text': row['confirmed_text']}
        prompt = cue(exercise_id, cue_id) if cue_id else None
        if result:
            # Historical completed reports also receive terminal copy, without rewriting progress.
            result['evaluation']['feedback'] = prompt
        # No expected unit text, original script, rubric, or preliminary feedback crosses this boundary.
        return {'state': state, 'cue_id': cue_id, 'prompt': prompt,
                'active_followup': conversation.active_followup if conversation else None,
                'followups_asked': len(conversation.followups) if conversation else 0,
                'pending_attempt': pending, 'result': result,
                'topic': public_topic(json.loads(exercise_or_404(exercise_id)['content']))}

    @app.get('/api/exercises/{exercise_id}/conversation')
    async def conversation_state(exercise_id: str):
        def read():
            exercise_or_404(exercise_id)
            return public_conversation(exercise_id)
        return await offload.run(read)

    @app.post('/api/exercises/{exercise_id}/conversation/listened')
    async def listened(exercise_id: str):
        async with lock:
            def mark():
                exercise_or_404(exercise_id)
                if repo.current_exercise()['id'] != exercise_id:
                    raise HTTPException(409, 'Resume the current exercise.')
                app.state.limits.admit_exercise('conversation.listened', exercise_id)
                if not repo.completed(exercise_id) and not repo.conversation(exercise_id):
                    repo.save_conversation(Conversation(exercise_id=exercise_id))
                return public_conversation(exercise_id)
            return await offload.run(mark)

    @app.post('/api/attempts/{attempt_id}/assess')
    async def assess(attempt_id: str, body: TurnConfirmation):
        async with timed_lock(settings, lock, 'turn.assessment'):
            def prepare():
                attempt = repo.attempt(attempt_id)
                if not attempt:
                    raise HTTPException(404, 'Attempt not found.')
                exercise_id = attempt['exercise_id']
                conversation = repo.conversation(exercise_id)
                if conversation and any(turn.attempt_id == attempt_id for turn in conversation.turns):
                    return 'done', public_conversation(exercise_id)  # Lost-response retry, no extra turn or adaptation.
                if repo.completed(exercise_id) or repo.current_exercise()['id'] != exercise_id:
                    raise HTTPException(409, 'This exercise is already complete.')
                lesson = repo.lesson()
                if lesson and (lesson.status != 'active' or lesson.phase != 'EXERCISE' or lesson.exercise_id != exercise_id):
                    raise HTTPException(409, 'Resume the active lesson before assessment.')
                app.state.limits.admit_exercise('turn.assess', exercise_id)
                if not body.confirmed or not body.text.strip():
                    raise HTTPException(422, 'Check and confirm what you said first.')
                recognized = Transcription.model_validate_json(attempt['transcription'])
                if body.hands_free and (body.text.strip() != recognized.text.strip()
                        or not reliable_recognition(recognized, settings.min_transcription_confidence)):
                    return 'done', JSONResponse(status_code=422, content={'detail': "I didn't quite catch that. Could you say it again?", 'uncertain': True})
                conversation = conversation or Conversation(exercise_id=exercise_id)
                question = conversation.active_followup
                if body.followup_id != (question.id if question else None):
                    raise HTTPException(409, 'This answer does not match the current question.')
                if conversation.pending_attempt_id and conversation.pending_attempt_id != attempt_id:
                    raise HTTPException(409, 'Use the current recorded response.')
                text = body.text.strip()
                conversation.pending_attempt_id, conversation.pending_text = attempt_id, text
                conversation.state = 'ASSESSING_FOLLOWUP' if question else 'ASSESSING'
                with operation(settings, 'turn.assessment_checkpoint_persistence'):
                    repo.prepare_assessment(attempt_id, text, conversation)  # Durable retry checkpoint before external work.
                turn = ConversationTurn(attempt_id=attempt_id, text=text, followup=question)
                turns = [*conversation.turns, turn]
                content = ExerciseContent.model_validate_json(exercise_or_404(exercise_id)['content'])
                return 'go', (exercise_id, lesson, conversation, turns, content)
            kind, staged = await offload.run(prepare)
            if kind == 'done':
                return staged
            exercise_id, lesson, conversation, turns, content = staged
            with operation(settings, 'ComprehensionEvaluator.assess_evidence'):
                assessment = await ai().assess(content, turns)
                validate_evidence(content, assessment, [turn.text for turn in turns])
            if assessment.transcription_concern:
                return JSONResponse(status_code=422, content={'detail': 'Please check the recognition or record again. No score or difficulty change was applied.', 'uncertain': True})

            def finish():
                conversation.turns, conversation.assessment = turns, assessment
                conversation.pending_attempt_id = conversation.pending_text = None
                # After the 10 minutes no further question is asked; unresolved evidence stays unknown.
                followup = None if app.state.limits.past_deadline(lesson) else choose_followup(conversation)
                if followup:
                    conversation.followups.append(followup)
                    conversation.active_followup = followup
                    conversation.state = 'AWAITING_FOLLOWUP'
                    repo.save_conversation(conversation)
                else:
                    evaluation = final_evaluation(content, assessment)
                    with operation(settings, 'turn.adaptive_decision'):
                        profile, event = adapt_final(repo.profile(), evaluation, settings)
                    evaluation.feedback = final_feedback(evaluation, event, settings.max_feedback_words, content, previous_advice(exercise_id))
                    conversation.active_followup = None
                    conversation.state = 'GIVING_FEEDBACK'
                    with operation(settings, 'ProgressRepository.complete_conversation'):
                        root = turns[0]
                        repo.complete(root.attempt_id, root.text, evaluation, event, profile, conversation)
                return public_conversation(exercise_id)
            return await offload.run(finish)

    @app.post('/api/exercises/{exercise_id}/conversation/ready')
    async def ready(exercise_id: str):
        async with lock:
            def mark_ready():
                exercise_or_404(exercise_id)
                if not repo.completed(exercise_id):
                    raise HTTPException(409, 'Complete the assessment first.')
                conversation = repo.conversation(exercise_id)
                if conversation:
                    conversation.state = 'READY_FOR_NEXT'
                    if settings.db_state_only_transitions:
                        repo.save_conversation_state(conversation)
                    else:
                        repo.save_conversation(conversation)
                return public_conversation(exercise_id)
            return await offload.run(mark_ready)

    @app.post('/api/exercises/{exercise_id}/coach-audio')
    async def coach_audio(exercise_id: str, body: CueRequest, request: Request):
        def check_checkpoint():
            if body.lesson_id is None:
                return  # Existing JSON/audio clients retain their owned cue contract.
            lesson = repo.lesson(body.lesson_id)
            if not lesson:
                raise HTTPException(404, 'Lesson not found.')
            if (lesson.status != 'active' or lesson.phase != 'EXERCISE'
                    or lesson.exercise_id != exercise_id or lesson.revision != body.lesson_revision):
                raise HTTPException(409, 'Reload your current lesson checkpoint.')
        async with timed_lock(settings, lock, 'turn.coach_audio'):
            def prepare():
                exercise_or_404(exercise_id)
                check_checkpoint()
                text = cue(exercise_id, body.cue_id)
                tutor = TutorSpeech(settings, ai(), repo.profile().tutor_voice, 'feedback')
                cache_id = tutor_cue_cache_id(tutor.settings, body.cue_id, text)
                name = repo.coach_audio(exercise_id, cache_id)
                # Exactly the original condition; the limit is consulted only when it would call the provider.
                regenerate = not name or name.endswith('-fallback-openai.mp3') or not restore_audio(name).exists()
                if regenerate and settings.lesson_limit_mode != 'off':
                    try:
                        # Final feedback belongs to the closing sequence; every other cue is practice.
                        app.state.limits.admit('wrapup' if body.cue_id == 'feedback' else 'practice', 'coach_audio',
                                               repo.lesson_for_exercise(exercise_id))
                    except LimitRefused:
                        try:
                            saved = bool(name) and restore_audio(name).exists()
                        except HTTPException:
                            saved = False
                        if not saved:
                            raise
                        regenerate = False  # A saved (even backup-voice) recording replays without new speech.
                return text, tutor, cache_id, name, regenerate
            text, tutor, cache_id, name, regenerate = await offload.run(prepare)
            if regenerate:
                with operation(settings, 'SpeechService.coach_audio'):
                    with operation(settings, 'turn.tts_provider'):
                        data = await tutor.speak(text)
                    kind = tutor.kind
                    with operation(settings, 'turn.tts_sanitization'):
                        data = await tutor.sanitize(data)

                def persist():
                    check_checkpoint()  # End can invalidate the lesson while the provider is running.
                    new_name = str(uuid4()) + ('-fallback-openai' if tutor.fallback else '') + '.' + kind
                    path = audio_dir / new_name
                    path.write_bytes(data)
                    try:
                        repo.save_coach_audio(exercise_id, cache_id, new_name, audio=data)
                    except Exception:
                        path.unlink(missing_ok=True)
                        raise
                    return new_name
                name = await offload.run(persist)
            if request.headers.get('accept') == 'audio/*':
                # Same owned, gated, sanitized and persisted cue; avoid a second
                # authenticated request before playback. JSON clients stay compatible.
                def load():
                    path = restore_audio(name)
                    return path.suffix, path.read_bytes()
                suffix, content = await offload.run(load)
                from app.services.tutor_tts import voice_headers
                return Response(content, media_type='audio/wav' if suffix == '.wav' else 'audio/mpeg',
                    headers=voice_headers(tutor.settings, name.endswith('-fallback-openai.mp3')))
            return {'audio_url': f'/api/exercises/{exercise_id}/coach-audio/{body.cue_id}'}

    @app.get('/api/exercises/{exercise_id}/coach-audio/{cue_id}')
    async def get_coach_audio(exercise_id: str, cue_id: str):
        exercise_or_404(exercise_id)
        text = cue(exercise_id, cue_id)  # Enforce gating even for guessed URLs.
        name = repo.coach_audio(exercise_id, audio_cache_id(cue_id, text))
        if not name or not restore_audio(name).exists():
            raise HTTPException(404, 'Coaching audio not generated yet. Please retry.')
        path = restore_audio(name)
        from app.services.tutor_tts import voice_headers
        return FileResponse(path, media_type='audio/wav' if path.suffix == '.wav' else 'audio/mpeg',
                            headers=voice_headers(settings, name.endswith('-fallback-openai.mp3')),
                            filename='audli-coach' + path.suffix)
