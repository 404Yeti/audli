"""Requests and jobs for AI lesson generation: the API side (create/view) and the generator process side (interpret, generate).

Phases a learner sees (Figma state machine): interpreted | queued → script → audio → transcript_sync → exercises → ready, or failed / rejected /
needs_input / unavailable. A phase only moves forward while this worker still holds the job (fenced), and `ready` is written in the same
transaction that publishes the lesson, so a lesson can never be presented as ready without being complete.
"""
import logging
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.listening import jobs, schema as t, store
from app.listening.audio_build import AudioQualityError, build_audio
from app.listening.domain import Cue
from app.listening.generation import (Deps, GenerationFailed, GenerationRefused, LLM_MICRO_PER_CALL, MIN_CREDIT_MARGIN, RETRYABLE_CHECKS, Spec,
                                      assemble, clamp_spec, credit_cost, estimate_chars, usd_micro_for_credits)
from app.listening.providers import ProviderPermanent, ProviderTemporary
from app.listening.speech import SpeechBudget, SpeechBudgetExceeded, SpeechPermanent, SpeechTemporary
from app.storage.repository import utcnow

log = logging.getLogger('audli.listening.generator')
INTERPRETATIONS_PER_DAY = 20
# What a learner may be told. Provider text and internals never reach them.
PUBLIC_FAILURE = {
    'rejected': 'Audli can’t make a lesson about that. Try a different topic.',
    'invalid_script': 'The lesson didn’t pass our quality checks. Nothing was saved. Please try again.',
    'audio_quality': 'The audio didn’t pass our quality checks. Nothing was saved. Please try again.',
    'provider_unavailable': 'Lesson creation is having trouble right now. Please try again in a little while.',
    'provider_quota': 'Lesson creation is unavailable right now.',
    'provider_auth': 'Lesson creation is unavailable right now.',
    'credits': 'Lesson creation is paused for now. Try again later.',
    'internal': 'Something went wrong. Nothing was saved. Please try again.',
}
LIVE_PHASES = ('queued', 'script', 'audio', 'transcript_sync', 'exercises')


# ---- API side ---------------------------------------------------------------------------------------------------------------------

def create_interpretation(db, user_id: str, client_request_id: str, text: str) -> tuple[str, bool]:
    """Idempotent by (learner, client_request_id). Limits how many interpretations one learner can ask for in a day."""
    existing = db.execute(select(t.generation_requests.c.id).where(and_(t.generation_requests.c.user_id == user_id,
                          t.generation_requests.c.client_request_id == client_request_id))).scalar_one_or_none()
    if existing:
        return existing, False
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from sqlalchemy import func
    today = db.execute(select(func.count()).select_from(t.generation_requests).where(and_(t.generation_requests.c.user_id == user_id,
                       t.generation_requests.c.kind == 'interpret', t.generation_requests.c.created_at >= start))).scalar_one()
    if today >= INTERPRETATIONS_PER_DAY:
        raise GenerationRefused('daily_limit')
    now, request_id = utcnow(), str(uuid4())
    db.execute(insert(t.generation_requests).values(id=request_id, user_id=user_id, client_request_id=client_request_id, kind='interpret', phase='queued',
               request_text=text, reserved_credits=0, reserved_micro=0, created_at=now, updated_at=now))
    jobs.enqueue(db, user_id, 'interpret', f'interpret:{request_id}', {'request_id': request_id}, max_attempts=3)
    return request_id, True


def create_generation(db, user_id: str, client_request_id: str, spec: Spec, notes: list[str], text: str | None) -> tuple[str, bool]:
    """Idempotent. Records the reviewed settings (phase 'unavailable' until finalize_reservation succeeds: no job exists and nothing is reserved yet)."""
    existing = db.execute(select(t.generation_requests.c.id).where(and_(t.generation_requests.c.user_id == user_id,
                          t.generation_requests.c.client_request_id == client_request_id))).scalar_one_or_none()
    if existing:
        return existing, False
    now, request_id = utcnow(), str(uuid4())
    db.execute(insert(t.generation_requests).values(id=request_id, user_id=user_id, client_request_id=client_request_id, kind='generate', phase='unavailable',
               request_text=text, spec={**spec.model_dump(), 'notes': notes}, reserved_credits=0, reserved_micro=0, created_at=now, updated_at=now))
    return request_id, True


def finalize_reservation(engine, ledger, settings, user_id: str, request_id: str, spec: Spec) -> None:
    """Second step of create_generation, in its own transaction: reserve, then queue the job. Raises GenerationRefused with nothing queued."""
    credits = credit_cost(settings.listening_gen_speech_model, estimate_chars(spec.minutes))
    micro = LLM_MICRO_PER_CALL * RETRYABLE_CHECKS + usd_micro_for_credits(credits, settings.listening_gen_usd_per_1k_credits)
    try:
        ledger.reserve(user_id, request_id, credits, micro)
    except GenerationRefused as refused:
        with engine.begin() as db:
            db.execute(update(t.generation_requests).where(t.generation_requests.c.id == request_id).values(phase='unavailable', failure_code=refused.reason, updated_at=utcnow(), finished_at=utcnow()))
        raise
    with engine.begin() as db:
        db.execute(update(t.generation_requests).where(t.generation_requests.c.id == request_id).values(phase='queued', updated_at=utcnow()))
        jobs.enqueue(db, user_id, 'generate', f'generate:{request_id}', {'request_id': request_id}, max_attempts=3)


def request_view(db, user_id: str, request_id: str) -> dict | None:
    """Owner only: another learner's id gets None (the API answers 404). Never includes provider text, keys or reservations."""
    row = db.execute(select(t.generation_requests).where(and_(t.generation_requests.c.id == request_id, t.generation_requests.c.user_id == user_id))).mappings().first()
    if row is None:
        return None
    spec = row['spec'] or {}
    code = row['failure_code']
    return {'id': row['id'], 'kind': row['kind'], 'phase': row['phase'], 'spec': {k: v for k, v in spec.items() if k != 'notes'} or None, 'notes': spec.get('notes', []),
            'question': row['question'], 'title': row['title'], 'lesson_id': row['lesson_id'],
            'failure': PUBLIC_FAILURE.get(code, PUBLIC_FAILURE['internal']) if code and row['phase'] in ('failed', 'rejected') else None,
            'unavailable_reason': code if row['phase'] == 'unavailable' else None, 'updated_at': row['updated_at']}


# ---- generator process side -------------------------------------------------------------------------------------------------------

def _set(engine, job, request_id: str, **values) -> None:
    """Write a phase change only while this worker still holds the job."""
    with engine.begin() as db:
        jobs.assert_current(db, job)
        db.execute(update(t.generation_requests).where(t.generation_requests.c.id == request_id).values(updated_at=utcnow(), **values))


def process(deps: Deps, job: jobs.Job, now=None) -> str:
    """Run one claimed interpret/generate job. Returns 'done', 'retry' or 'failed'; never raises on provider or validation problems."""
    request_id = job.payload.get('request_id', '')
    try:
        if job.kind == 'interpret':
            run_interpret(deps, job, request_id)
        elif job.kind == 'generate':
            run_generate(deps, job, request_id)
        else:
            raise GenerationFailed('internal')
        with deps.engine.begin() as db:
            jobs.succeed(db, job, now=now)
        return 'done'
    except jobs.LeaseLost:
        return 'lost'
    except (ProviderTemporary, SpeechTemporary) as error:
        code = getattr(error, 'code', 'provider_unavailable')
        with deps.engine.begin() as db:
            (jobs.retry_later if job.attempts < job.max_attempts else jobs.fail)(db, job, code if isinstance(code, str) else 'provider_unavailable', now=now)
        if job.attempts >= job.max_attempts:
            _give_up(deps, request_id, 'provider_unavailable')
        return 'retry' if job.attempts < job.max_attempts else 'failed'
    except GenerationFailed as error:
        _give_up(deps, request_id, error.code)
        with deps.engine.begin() as db:
            jobs.fail(db, job, error.code, now=now)
        return 'failed'
    except (ProviderPermanent, SpeechPermanent, SpeechBudgetExceeded, AudioQualityError) as error:
        code = getattr(error, 'code', None) or ('audio_quality' if isinstance(error, AudioQualityError) else 'provider_unavailable')
        _give_up(deps, request_id, code)
        with deps.engine.begin() as db:
            jobs.fail(db, job, code[:60], now=now)
        return 'failed'
    except Exception:
        log.exception('generation job crashed')            # type only in the job row; the traceback stays in the operator log (no learner text, no keys)
        _give_up(deps, request_id, 'internal')
        with deps.engine.begin() as db:
            jobs.fail(db, job, 'internal', now=now)
        return 'failed'


def maintenance(deps: Deps, kinds: tuple[str, ...], now=None) -> int:
    """Settle generation jobs that ran out of tries with their lease expired (a crashed or stalled generator): fail the request, release its reservation."""
    with deps.engine.begin() as db:
        payloads = jobs.reap_exhausted(db, now, kinds=kinds)
    for payload in payloads:
        _give_up(deps, str(payload.get('request_id', '')), 'provider_unavailable')
    return len(payloads)


def _give_up(deps: Deps, request_id: str, code: str) -> None:
    """Mark the request failed and give back whatever was reserved but not spent. Credits already used stay counted."""
    try:
        with deps.engine.begin() as db:
            row = db.execute(select(t.generation_requests.c.actual_credits, t.generation_requests.c.kind, t.generation_requests.c.phase)
                             .where(t.generation_requests.c.id == request_id)).first()
            if row is None or row.phase in ('ready', 'failed', 'rejected'):
                return
            db.execute(update(t.generation_requests).where(t.generation_requests.c.id == request_id).values(
                phase='rejected' if code == 'rejected' else 'failed', failure_code=code, updated_at=utcnow(), finished_at=utcnow()))
        if row.kind == 'generate':
            deps.ledger.settle(request_id, row.actual_credits, release=row.actual_credits is None)
    except Exception:
        log.exception('could not close a failed generation request')


def run_interpret(deps: Deps, job: jobs.Job, request_id: str) -> None:
    with deps.engine.begin() as db:
        text = db.execute(select(t.generation_requests.c.request_text).where(t.generation_requests.c.id == request_id)).scalar_one()
    if deps.halted and deps.halted():
        raise ProviderTemporary('paused', 'provider_unavailable')
    raw = deps.interpreter.interpret(text, deps.settings.listening_gen_max_minutes)
    if raw.get('rejected_reason'):
        raise GenerationFailed('rejected')
    if not (raw.get('topic') or '').strip():
        _set(deps.engine, job, request_id, phase='needs_input', question=(raw.get('question') or 'What would you like the lesson to be about?')[:300])
        return
    spec, notes = clamp_spec({k: raw.get(k) for k in ('topic', 'cefr', 'accent', 'minutes', 'format', 'scenario')}, deps.settings.listening_gen_max_minutes)
    if raw.get('accent') and raw['accent'].lower() not in ('american', 'british', 'australian') and 'accent_unavailable' not in notes:
        notes.append('accent_unavailable')
    try:
        validated = Spec.model_validate(spec)
    except Exception:
        raise GenerationFailed('rejected') from None
    _set(deps.engine, job, request_id, phase='interpreted', spec={**validated.model_dump(), 'notes': notes}, question=None)


def run_generate(deps: Deps, job: jobs.Job, request_id: str) -> None:
    from app.listening.objects import ObjectMissing
    with deps.engine.begin() as db:
        row = db.execute(select(t.generation_requests).where(t.generation_requests.c.id == request_id)).mappings().one()
    if row['phase'] in ('ready', 'failed', 'rejected'):
        return                                                    # a re-delivered job for a finished request does nothing
    spec = Spec.model_validate({k: v for k, v in row['spec'].items() if k != 'notes'})
    user_id, settings = row['user_id'], deps.settings
    if deps.halted and deps.halted():
        raise ProviderTemporary('paused', 'provider_unavailable')
    max_chars = estimate_chars(spec.minutes)
    audio_meta = row['audio_meta']
    content = None
    if audio_meta is None:                                        # nothing has been paid for yet: write and check the script
        _set(deps.engine, job, request_id, phase='script')
        feedback = None
        for attempt in range(RETRYABLE_CHECKS):
            raw = deps.writer.write(spec, max_chars, feedback)
            try:
                content = assemble(spec, raw, request_id, max_chars)
                break
            except GenerationFailed:
                raise
            except ValueError as problem:
                feedback = str(problem)[:300]
                log.info('script attempt %d failed validation', attempt + 1)
        if content is None:
            raise GenerationFailed('invalid_script')
        chars = sum(len(s.text) for s in content.sentences)
        exact = credit_cost(settings.listening_gen_speech_model, chars)
        with deps.engine.begin() as db:
            jobs.assert_current(db, job)
            reserved = db.execute(select(t.generation_requests.c.reserved_credits).where(t.generation_requests.c.id == request_id)).scalar_one()
        if exact > reserved:
            raise GenerationFailed('credits')                      # never spend more than was reserved
        remaining = deps.speech.remaining_credits()
        if remaining is None or remaining - exact < MIN_CREDIT_MARGIN:
            raise GenerationFailed('credits')                      # the provider itself is nearly out: do not start
        _set(deps.engine, job, request_id, phase='audio', title=content.title)
        voices = deps.voices[spec.accent]
        speaker_voice = {s.id: voices[i % len(voices)] for i, s in enumerate(content.speakers)}
        deps.speech.budget = SpeechBudget(chars)
        deps.speech.spent = 0.0
        built = build_audio(content, deps.speech, speaker_voice)
        key = f'lessons/{content.slug}/{built.sha256[:16]}.mp3'
        deps.objects.put(key, built.data, 'audio/mpeg')
        if not deps.objects.exists(key):
            raise GenerationFailed('internal')                     # never continue to publish audio that is not actually stored
        actual = int(round(deps.speech.spent)) if deps.speech.spent else exact
        meta = {'content': content.model_dump(mode='json'), 'voice': ' + '.join(f'{s}={v}' for s, v in sorted(speaker_voice.items()))[:60],
                'sha256': built.sha256, 'size_bytes': len(built.data), 'duration_ms': built.duration_ms, 'cues': [c.model_dump() for c in built.cues]}
        _set(deps.engine, job, request_id, phase='transcript_sync', audio_key=key, audio_meta=meta, actual_credits=min(actual, reserved))
        audio_meta, audio_key = meta, key
    else:
        from app.listening.domain import LessonContent
        content = LessonContent.model_validate(audio_meta['content'])
        audio_key = row['audio_key']
        if not deps.objects.exists(audio_key):
            raise GenerationFailed('internal')
    _set(deps.engine, job, request_id, phase='exercises')
    audio = store.AudioFile(accent=spec.accent, voice=audio_meta['voice'], provider='elevenlabs', object_key=audio_key, content_type='audio/mpeg',
                            sha256=audio_meta['sha256'], size_bytes=audio_meta['size_bytes'], duration_ms=audio_meta['duration_ms'],
                            cues=[Cue(**c) for c in audio_meta['cues']])
    with deps.engine.begin() as db:
        jobs.assert_current(db, job)
        version_id, _ = store.publish_version(db, content, audio)
        lesson_id = db.execute(select(t.lessons.c.id).where(t.lessons.c.slug == content.slug)).scalar_one()
        if db.execute(select(t.lesson_owners.c.lesson_id).where(t.lesson_owners.c.lesson_id == lesson_id)).first() is None:
            db.execute(insert(t.lesson_owners).values(lesson_id=lesson_id, user_id=user_id, created_at=utcnow()))
        db.execute(update(t.generation_requests).where(t.generation_requests.c.id == request_id).values(
            phase='ready', lesson_id=lesson_id, title=content.title, updated_at=utcnow(), finished_at=utcnow(), audio_meta=None))
    with deps.engine.begin() as db:
        actual = db.execute(select(t.generation_requests.c.actual_credits).where(t.generation_requests.c.id == request_id)).scalar_one()
    deps.ledger.settle(request_id, actual)                         # replace the estimate by what was really used


class MeteredSpeech:
    """Wraps a speech provider: forwards the character budget, adds up the credits the provider says it charged, and exposes its remaining credits."""
    def __init__(self, inner, multiplier: float):
        self.inner, self.multiplier, self.spent = inner, multiplier, 0.0
        self.name = getattr(inner, 'name', 'speech')

    @property
    def budget(self):
        return getattr(self.inner, 'budget', None)

    @budget.setter
    def budget(self, value):
        self.inner.budget = value

    def synthesize(self, text: str, voice: str) -> bytes:
        data = self.inner.synthesize(text, voice)
        cost = getattr(self.inner, 'last_character_cost', None)
        self.spent += cost if cost is not None else len(text) * self.multiplier
        return data

    def remaining_credits(self) -> int | None:
        return self.inner.remaining_credits() if hasattr(self.inner, 'remaining_credits') else None
