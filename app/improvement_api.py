"""User-triggered, idempotent, bounded advisory language coaching for a saved answer (AUD-77).

Guarantees, each covered by tests:
- Registered only when AUDLI_REVIEW_IMPROVEMENT_ENABLED is on. Otherwise the route does not exist.
- Authenticated, owner-scoped, and only for an exercise whose assessment is complete.
- Never reads or writes scores, adaptation, evidence or lesson state, and never touches the daily lesson allowance.
- The provider receives only the learner's response text. It is never given the script or expected answers.
- No account-wide lock and no open database transaction is held while a provider call is in flight.
- At most `review_improvement_daily_limit` new attempts per learner per rolling 24 hours. Repeating the request,
  reopening a stored result and every report read are free. Failed and abandoned claims do not consume the allowance.
- Recovery (bounded): one automatic repair for narrowly defined formatting failures (same attempt, no extra allowance);
  one learner-initiated retry (`{"retry": true}`) of a rejected result per exercise per prompt version, which is a new
  attempt counted against the same rolling allowance; older-prompt-version rejections become retryable without bypassing it.
- Every refusal carries a stable `improvement.code`, and every outcome is logged and counted by rejection code.
"""
import asyncio
import logging
from time import perf_counter
from uuid import UUID

from fastapi import HTTPException
from pydantic import StrictBool
from fastapi.responses import JSONResponse

from app.improvement import PROMPT_VERSION, ImprovementState, eligibility, coach, rejection_layer
from app.models import StrictModel
from app.telemetry import aggregator
from app.storage import improvement as store
from app.storage.repository import learner_tag, utcnow

LOG = logging.getLogger('uvicorn.error')
BUSY = 'Audli is preparing other suggestions right now. Try again in a moment.'
FAILED = "Audli couldn't improve this answer right now. Try again in a moment."


class ImproveRequest(StrictModel):
    retry: StrictBool = False


def _error(status: int, code: str, detail: str):
    return JSONResponse(status_code=status, content={'detail': detail, 'improvement': {'code': code}})


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
    async def improve(exercise_id: UUID, body: ImproveRequest | None = None):
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

        retry = bool(body and body.retry)
        claim = store.claim(repo, exercise_id, answer['attempt_id'], text, PROMPT_VERSION,
                            settings.review_improvement_daily_limit, at, retry=retry)
        if claim.kind in ('existing', 'final'):
            state = store.state_from_row(claim.row, text, at, retry_available=claim.retry_available)
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
            return _error(503, 'improvement_busy', BUSY)
        status, outcome, started, repaired = 'failed', {}, perf_counter(), False
        await slots.acquire()
        try:
            async with asyncio.timeout(settings.review_improvement_timeout_seconds):
                decision = await coach(ai(), text)
            repaired = decision.repaired
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
        stored = store.finish(repo, claim.id, app.state.review_clock(), status, model=settings.llm_model, repaired=repaired, **outcome)
        elapsed = (perf_counter() - started) * 1000
        LOG.info('review_improvement outcome=%s rejection=%s layer=%s repaired=%s retry=%s stored=%s elapsed_ms=%.0f learner=%s', status,
                 outcome.get('rejection'), rejection_layer(outcome.get('rejection')), repaired, retry, stored, elapsed, learner_tag(repo.learner_id))
        if settings.latency_aggregates:  # Numeric-only: a handful of fixed names, never text, IDs or codes from the learner.
            aggregator.record(f'review_improvement.{status}', elapsed, status != 'failed')
            if status == 'rejected':
                aggregator.record(f"review_improvement.rejected.layer{rejection_layer(outcome.get('rejection')) or 'x'}", elapsed)
            if repaired:
                aggregator.record('review_improvement.repaired', elapsed)
        if status == 'failed':
            return _error(503, 'improvement_failed', FAILED)
        live = store.live_row(repo, exercise_id)
        again = live is not None and live['status'] == 'rejected' and store.can_retry(repo, exercise_id, PROMPT_VERSION)
        state = store.state_from_row(live, text, app.state.review_clock(), retry_available=again) if live else None
        return respond(state or ImprovementState(state='pending'), 200 if state else 202)
