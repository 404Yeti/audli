"""Turn-based coaching routes alongside the unchanged V0.1 evaluation endpoint."""
import json
from hashlib import sha256
from uuid import uuid4
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from app.audio import sanitize_generated_audio
from app.conversation import validate_evidence, choose_followup, final_evaluation, adapt_final
from app.feedback import final_feedback
from app.models import EvidenceEvaluation, Evaluation
from app.diagnostics import operation
from app.models import StrictModel, Conversation, ConversationTurn, ExerciseContent

SUMMARY_PROMPT = 'Tell me what you understood. Use your own words; grammar does not matter.'

class TurnConfirmation(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    confirmed: bool
    followup_id: str | None = None

class CueRequest(StrictModel):
    cue_id: str = Field(min_length=1, max_length=80)


def register_conversation_routes(app, repo, ai, lock, settings, audio_dir, exercise_or_404, restore_audio):
    def audio_cache_id(cue_id, text):
        # Old evaluator-report audio must never replay as terminal feedback.
        return ('feedback-v0.2.1-' + sha256(text.encode()).hexdigest()[:16]
                if cue_id == 'feedback' else cue_id)

    def cue(exercise_id, cue_id):
        conversation = repo.conversation(exercise_id)
        completed = repo.completed(exercise_id)
        if cue_id == 'feedback':
            if not completed:
                raise HTTPException(403, 'Complete assessment before feedback.')
            result = repo.result(completed)
            model = EvidenceEvaluation if result['evaluation'].get('version') == 'v0.2' else Evaluation
            evaluation = model.model_validate(result['evaluation'])
            return final_feedback(evaluation, SimpleNamespace(**result['adaptation']), settings.max_feedback_words)
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
                'pending_attempt': pending, 'result': result}

    @app.get('/api/exercises/{exercise_id}/conversation')
    async def conversation_state(exercise_id: str):
        exercise_or_404(exercise_id)
        return public_conversation(exercise_id)

    @app.post('/api/exercises/{exercise_id}/conversation/listened')
    async def listened(exercise_id: str):
        async with lock:
            exercise_or_404(exercise_id)
            if repo.current_exercise()['id'] != exercise_id:
                raise HTTPException(409, 'Resume the current exercise.')
            if not repo.completed(exercise_id) and not repo.conversation(exercise_id):
                repo.save_conversation(Conversation(exercise_id=exercise_id))
            return public_conversation(exercise_id)

    @app.post('/api/attempts/{attempt_id}/assess')
    async def assess(attempt_id: str, body: TurnConfirmation):
        async with lock:
            attempt = repo.attempt(attempt_id)
            if not attempt:
                raise HTTPException(404, 'Attempt not found.')
            exercise_id = attempt['exercise_id']
            conversation = repo.conversation(exercise_id)
            if conversation and any(turn.attempt_id == attempt_id for turn in conversation.turns):
                return public_conversation(exercise_id)  # Lost-response retry, no extra turn or adaptation.
            if repo.completed(exercise_id) or repo.current_exercise()['id'] != exercise_id:
                raise HTTPException(409, 'This exercise is already complete.')
            if not body.confirmed or not body.text.strip():
                raise HTTPException(422, 'Check and confirm what you said first.')
            conversation = conversation or Conversation(exercise_id=exercise_id)
            question = conversation.active_followup
            if body.followup_id != (question.id if question else None):
                raise HTTPException(409, 'This answer does not match the current question.')
            if conversation.pending_attempt_id and conversation.pending_attempt_id != attempt_id:
                raise HTTPException(409, 'Use the current recorded response.')
            text = body.text.strip()
            conversation.pending_attempt_id, conversation.pending_text = attempt_id, text
            conversation.state = 'ASSESSING_FOLLOWUP' if question else 'ASSESSING'
            repo.confirm_text(attempt_id, text)
            repo.save_conversation(conversation)  # Retry/reload checkpoint before external work.
            turn = ConversationTurn(attempt_id=attempt_id, text=text, followup=question)
            turns = [*conversation.turns, turn]
            content = ExerciseContent.model_validate_json(exercise_or_404(exercise_id)['content'])
            with operation(settings, 'ComprehensionEvaluator.assess_evidence'):
                assessment = await ai().assess(content, turns)
                validate_evidence(content, assessment, [turn.text for turn in turns])
            if assessment.transcription_concern:
                return JSONResponse(status_code=422, content={'detail': 'Please check the recognition or record again. No score or difficulty change was applied.', 'uncertain': True})
            conversation.turns, conversation.assessment = turns, assessment
            conversation.pending_attempt_id = conversation.pending_text = None
            followup = choose_followup(conversation)
            if followup:
                conversation.followups.append(followup)
                conversation.active_followup = followup
                conversation.state = 'AWAITING_FOLLOWUP'
                repo.save_conversation(conversation)
            else:
                evaluation = final_evaluation(content, assessment)
                profile, event = adapt_final(repo.profile(), evaluation, settings)
                evaluation.feedback = final_feedback(evaluation, event, settings.max_feedback_words)
                conversation.active_followup = None
                conversation.state = 'GIVING_FEEDBACK'
                with operation(settings, 'ProgressRepository.complete_conversation'):
                    root = turns[0]
                    repo.complete(root.attempt_id, root.text, evaluation, event, profile, conversation)
            return public_conversation(exercise_id)

    @app.post('/api/exercises/{exercise_id}/conversation/ready')
    async def ready(exercise_id: str):
        async with lock:
            exercise_or_404(exercise_id)
            if not repo.completed(exercise_id):
                raise HTTPException(409, 'Complete the assessment first.')
            conversation = repo.conversation(exercise_id)
            if conversation:
                conversation.state = 'READY_FOR_NEXT'
                repo.save_conversation(conversation)
            return public_conversation(exercise_id)

    @app.post('/api/exercises/{exercise_id}/coach-audio')
    async def coach_audio(exercise_id: str, body: CueRequest):
        async with lock:
            exercise_or_404(exercise_id)
            text = cue(exercise_id, body.cue_id)
            cache_id = audio_cache_id(body.cue_id, text)
            name = repo.coach_audio(exercise_id, cache_id)
            if not name or not restore_audio(name).exists():
                with operation(settings, 'SpeechService.coach_audio'):
                    data = await ai().speak(text)
                    kind = 'wav' if settings.provider == 'demo' else 'mp3'
                    data = await sanitize_generated_audio(data, kind)
                name = str(uuid4()) + '.' + kind
                path = audio_dir / name
                path.write_bytes(data)
                try:
                    repo.save_coach_audio(exercise_id, cache_id, name, audio=data)
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
            return {'audio_url': f'/api/exercises/{exercise_id}/coach-audio/{body.cue_id}'}

    @app.get('/api/exercises/{exercise_id}/coach-audio/{cue_id}')
    async def get_coach_audio(exercise_id: str, cue_id: str):
        exercise_or_404(exercise_id)
        text = cue(exercise_id, cue_id)  # Enforce gating even for guessed URLs.
        name = repo.coach_audio(exercise_id, audio_cache_id(cue_id, text))
        if not name or not restore_audio(name).exists():
            raise HTTPException(404, 'Coaching audio not generated yet. Please retry.')
        path = restore_audio(name)
        return FileResponse(path, media_type='audio/wav' if path.suffix == '.wav' else 'audio/mpeg',
                            filename='audli-coach' + path.suffix)
