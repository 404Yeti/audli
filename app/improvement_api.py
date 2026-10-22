"""User-triggered, idempotent, bounded advisory language coaching for a saved answer (AUD-77).

Guarantees, each covered by tests:
- Registered only when AUDLI_REVIEW_IMPROVEMENT_ENABLED is on. Otherwise the route does not exist.
- Authenticated, owner-scoped, and only for an exercise whose assessment is complete.
- Never reads or writes scores, adaptation, evidence or lesson state, and never touches the daily lesson allowance.
- The provider receives only the learner's response text. It is never given the script or expected answers.
- No account-wide lock and no open database transaction is held while a provider call is in flight.
- At most `review_improvement_daily_limit` new attempts per learner per rolling 24 hours. Repeating the request,
  reopening a stored result and every report read are free. Failed and abandoned claims do not consume the allowance.
"""
import asyncio
import logging
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.improvement import PROMPT_VERSION, ImprovementState, eligibility, coach
from app.storage import improvement as store
from app.storage.repository import learner_tag, utcnow

LOG = logging.getLogger('uvicorn.error')
BUSY = 'Audli is preparing other suggestions right now. Try again in a moment.'
FAILED = "Audli couldn't improve this answer right now. Try again in a moment."


def _limited(code: str, detail: str, retry_after: int):
    return JSONResponse(status_code=429, content={'detail': detail, 'improvement': {'code': code, 'retry_after_seconds': retry_after}},
                        headers={'Retry-After': str(retry_after)})


def _wait(seconds: int) -> str:
    return 'less than a minute' if seconds < 60 else f'about {max(1, round(seconds / 60))} minutes' if seconds < 5400 else f'about {max(1, round(seconds / 3600))} hours'


def register_improvement_routes(app, repo, ai, settings):
    if not settings.review_improvement_enabled:
        return
    app.state.review_clock = utcnow
    slots = asyncio.Semaphore(settings.review_improvement_max_concurrent)

    def respond(state: ImprovementState, status: int = 200):
        return JSONResponse(status_code=status, content={'improvement': state.model_dump(mode='json')})

    @app.post('/api/exercises/{exercise_id}/review/improvement')
    async def improve(exercise_id: UUID):
        exercise_id = str(exercise_id)
        if not repo.exercise(exercise_id):
            raise HTTPException(404, 'Exercise not found.')
        if not repo.completed(exercise_id):
            raise HTTPException(403, 'Complete the assessment before asking for language suggestions.')
        answer = store.first_answer(repo, exercise_id)
        if answer is None:
            raise HTTPException(404, 'No saved response for this passage.')
        text, reason = eligibility(answer['text'], answer.get('transcription'), answer['text'], settings.min_transcription_confidence)
        at = app.state.review_clock()
        if text is None:
            live = store.live_row(repo, exercise_id)  # A stored result stays readable even if rules tighten later.
            if live is not None and (state := store.state_from_row(live, answer['text'], at)) is not None:
                return respond(state)
            return respond(ImprovementState(state='unavailable', reason=reason))

        claim = store.claim(repo, exercise_id, answer['attempt_id'], text, PROMPT_VERSION,
                            settings.review_improvement_daily_limit, at)
        if claim.kind == 'existing':
            state = store.state_from_row(claim.row, text, at)
            if state is None:
                raise HTTPException(409, 'This answer changed. Reload your review.')
            return respond(state)
        if claim.kind == 'pending':
            return respond(ImprovementState(state='pending'), 202)
        if claim.kind == 'limit':
            return _limited('improvement_limit', f'You have used today’s language suggestions. More open in {_wait(claim.retry_after)}.', claim.retry_after)
        if claim.kind == 'cooldown':
            return _limited('improvement_cooldown', 'That did not work a moment ago. Wait a few seconds and try again.', claim.retry_after)
        if claim.kind == 'burst':
            return _limited('improvement_unavailable', 'Language suggestions are paused for a little while after repeated problems.', claim.retry_after)

        # A claim is held: from here to the completion below there is no open transaction and no lock.
        if slots.locked():
            store.finish(repo, claim.id, app.state.review_clock(), 'abandoned')
            raise HTTPException(503, BUSY)
        status, outcome = 'failed', {}
        await slots.acquire()
        try:
            async with asyncio.timeout(settings.review_improvement_timeout_seconds):
                decision = await coach(ai(), text)
            if decision.status == 'ready':
                status, outcome = 'ready', dict(improved_text=decision.improved_text, notes=decision.notes)
            else:
                status, outcome = 'rejected', dict(rejection=decision.rejection)
        except asyncio.CancelledError:
            store.finish(repo, claim.id, app.state.review_clock(), 'abandoned')
            raise
        except Exception as error:
            # The type only: provider errors can carry the learner's text.
            LOG.warning('review_improvement failed error=%s learner=%s', type(error).__name__, learner_tag(repo.learner_id))
        finally:
            slots.release()
        stored = store.finish(repo, claim.id, app.state.review_clock(), status, model=settings.llm_model, **outcome)
        LOG.info('review_improvement outcome=%s rejection=%s stored=%s learner=%s', status, outcome.get('rejection'), stored, learner_tag(repo.learner_id))
        if status == 'failed':
            raise HTTPException(503, FAILED)
        live = store.live_row(repo, exercise_id)
        state = store.state_from_row(live, text, app.state.review_clock()) if live else None
        return respond(state or ImprovementState(state='pending'), 200 if state else 202)
