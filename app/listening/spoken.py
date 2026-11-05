"""The spoken exercise: accepting a recording, skipping, retrying, and the worker's fenced steps.

Request path (API process): validate -> claim the upload in the database -> store the temporary recording -> mark it pending and enqueue a
job in the same transaction. The learner then moves on; nothing here waits for transcription or assessment.

Worker path: transcribe -> store the learner's words -> delete the recording -> classify key points -> store the grounded result. Every
worker write first proves it still holds the job's latest fence (jobs.assert_current), in the transaction that writes the result.
"""
import secrets
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, select, update, insert

from app.listening import jobs, schema as t
from app.listening.domain import MIN_WORDS, ground_assessment, word_count
from app.listening.objects import ObjectMissing
from app.listening.providers import AssessmentInput, ProviderPermanent, ProviderTemporary
from app.listening.store import Conflict, Invalid, NotFound, MAX_SPOKEN_TRIES, _owned_attempt, _settle
from app.storage.repository import utcnow

RECEIVING_GRACE = timedelta(minutes=10)     # an upload claimed but never finished is abandoned after this
RECORDING_LIFETIME = timedelta(hours=1)     # no temporary recording outlives this, whatever happened to its job


class Limited(Exception):
    pass


def _spoken_exercise(db, attempt, exercise_id: str):
    exercise = db.execute(select(t.exercises.c.id, t.exercises.c.kind).where(and_(
        t.exercises.c.id == exercise_id, t.exercises.c.version_id == attempt['version_id']))).mappings().first()
    if not exercise or exercise['kind'] != 'spoken':
        raise NotFound('Question not found in this attempt.')


def check_target(db, user_id: str, attempt_id: str, exercise_id: str) -> None:
    """Cheap ownership and kind check so nobody else's ids make the server decode audio or touch storage."""
    _spoken_exercise(db, _owned_attempt(db, user_id, attempt_id), exercise_id)


def _row(db, attempt_id: str, exercise_id: str):
    return db.execute(select(t.spoken).where(and_(t.spoken.c.attempt_id == attempt_id, t.spoken.c.exercise_id == exercise_id))).mappings().first()


def recording_key(attempt_id: str, exercise_id: str, generation: int, kind: str) -> str:
    return f'listening-tmp/{attempt_id}/{exercise_id}/g{generation}-{secrets.token_urlsafe(18)}.{kind}'


def claim_upload(db, user_id: str, attempt_id: str, exercise_id: str, upload_id: str, kind: str, content_type: str,
                 daily_limit: int, now=None) -> tuple[str, str | None, int]:
    """Returns (action, key, generation). action is 'store' (write the object), or 'replay' (this exact upload already exists)."""
    now = now or utcnow()
    attempt = _owned_attempt(db, user_id, attempt_id, lock=True)
    _spoken_exercise(db, attempt, exercise_id)
    row = _row(db, attempt_id, exercise_id)
    if row and row['upload_id'] == upload_id:
        if row['status'] == 'receiving':
            return 'store', row['recording_key'], row['generation']      # the client is retrying an interrupted upload
        return 'replay', None, row['generation']
    if row and row['status'] != 'failed':
        raise Conflict('This question already has an answer.' if row['status'] != 'receiving' else 'Another recording is still arriving.')
    generation = (row['generation'] if row else 0) + 1
    if generation > MAX_SPOKEN_TRIES:
        raise Conflict('That is the most tries for this question. Continue to the next one.')
    recent = db.execute(select(func.count()).select_from(t.jobs).where(and_(t.jobs.c.user_id == user_id, t.jobs.c.kind == 'spoken',
                        t.jobs.c.created_at > now - timedelta(hours=24)))).scalar_one()
    if recent >= daily_limit:
        raise Limited()
    key = recording_key(attempt_id, exercise_id, generation, kind)
    values = dict(status='receiving', generation=generation, upload_id=upload_id, recording_key=key, recording_type=content_type,
                  transcript=None, assessment=None, failure_code=None, updated_at=now)
    if row:
        db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == attempt_id, t.spoken.c.exercise_id == exercise_id)).values(**values))
    else:
        db.execute(insert(t.spoken).values(attempt_id=attempt_id, exercise_id=exercise_id, version_id=attempt['version_id'], created_at=now, **values))
    return 'store', key, generation


def finish_upload(db, user_id: str, attempt_id: str, exercise_id: str, upload_id: str, now=None) -> None:
    """The recording is stored: make the answer pending and enqueue its job, atomically. Safe to repeat."""
    now = now or utcnow()
    attempt = _owned_attempt(db, user_id, attempt_id, lock=True)
    row = _row(db, attempt_id, exercise_id)
    if row is None or row['upload_id'] != upload_id:
        raise Conflict('This recording was replaced by a newer one.')
    if row['status'] != 'receiving':
        return
    db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == attempt_id, t.spoken.c.exercise_id == exercise_id))
               .values(status='pending', updated_at=now))
    jobs.enqueue(db, user_id, 'spoken', f"spoken:{attempt_id}:{exercise_id}:{row['generation']}",
                 {'attempt_id': attempt_id, 'exercise_id': exercise_id, 'generation': row['generation']}, now=now)
    _settle(db, attempt)


def skip(db, user_id: str, attempt_id: str, exercise_id: str, now=None) -> None:
    now = now or utcnow()
    attempt = _owned_attempt(db, user_id, attempt_id, lock=True)
    _spoken_exercise(db, attempt, exercise_id)
    row = _row(db, attempt_id, exercise_id)
    if row:
        if row['status'] == 'skipped':
            return
        raise Conflict('This question already has an answer.')
    if attempt['status'] != 'in_progress':
        raise Conflict('This attempt is already complete.')
    db.execute(insert(t.spoken).values(attempt_id=attempt_id, exercise_id=exercise_id, version_id=attempt['version_id'], status='skipped',
                                       generation=0, created_at=now, updated_at=now))
    _settle(db, attempt)


def retry_assessment(db, user_id: str, attempt_id: str, exercise_id: str, now=None) -> None:
    """Assess the learner's saved words again. Only possible when transcription already succeeded (the audio is gone by design)."""
    now = now or utcnow()
    attempt = _owned_attempt(db, user_id, attempt_id, lock=True)
    _spoken_exercise(db, attempt, exercise_id)
    row = _row(db, attempt_id, exercise_id)
    if row is None or row['status'] != 'failed':
        raise Conflict('There is nothing to retry for this question.')
    if not row['transcript']:
        raise Invalid('We no longer have your recording. Record your answer again.')
    generation = row['generation'] + 1
    if generation > MAX_SPOKEN_TRIES:
        raise Conflict('That is the most tries for this question. Continue to the next one.')
    db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == attempt_id, t.spoken.c.exercise_id == exercise_id))
               .values(status='pending', generation=generation, failure_code=None, upload_id=f'retry-{uuid4().hex[:16]}', updated_at=now))
    jobs.enqueue(db, user_id, 'spoken', f'spoken:{attempt_id}:{exercise_id}:{generation}',
                 {'attempt_id': attempt_id, 'exercise_id': exercise_id, 'generation': generation}, now=now)


# ---- worker side ------------------------------------------------------------------------------------------------------------

class Superseded(Exception):
    """The answer this job was for has been replaced or settled; there is nothing left to do."""


def _current(db, job):
    row = _row(db, job.payload['attempt_id'], job.payload['exercise_id'])
    if row is None or row['status'] != 'pending' or row['generation'] != job.payload['generation']:
        raise Superseded(job.id)
    return row


def process(engine, job, objects, transcriber, assessor, now=None) -> str:
    """Run one claimed spoken job to a settled state. Returns 'done', 'retry', 'failed', 'superseded' or 'lost'."""
    now_ = (lambda: now) if now else utcnow
    attempt_id, exercise_id = job.payload['attempt_id'], job.payload['exercise_id']
    try:
        with engine.begin() as db:
            jobs.assert_current(db, job)
            row = _current(db, job)
            transcript, key, content_type = row['transcript'], row['recording_key'], row['recording_type']
            exercise = db.execute(select(t.exercises.c.prompt, t.exercises.c.version_id).where(t.exercises.c.id == exercise_id)).mappings().one()
            rubric = db.execute(select(t.rubrics.c.data).where(t.rubrics.c.exercise_id == exercise_id)).scalar_one()
            version = db.execute(select(t.versions.c.title, t.versions.c.sentences).where(t.versions.c.id == exercise['version_id'])).mappings().one()
        if transcript is None:
            if not key:
                return _fail(engine, objects, job, 'recording_missing', now_(), False)
            try:
                audio = objects.get(key)
            except ObjectMissing:
                return _fail(engine, objects, job, 'recording_missing', now_(), False)
            with engine.begin() as db:
                jobs.heartbeat(db, job, now=now_())
            heard = transcriber.transcribe(audio, content_type or 'audio/webm')
            # Unreliable audio (the provider could not detect speech) is stored as no words at all: never show invented text as theirs.
            transcript = heard.text.strip()[:2000] if heard.reliable else ''
            with engine.begin() as db:
                jobs.assert_current(db, job)
                db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == attempt_id, t.spoken.c.exercise_id == exercise_id))
                           .values(transcript=transcript, updated_at=now_()))
        _discard_recording(engine, objects, job, key, now_())       # the audio's only purpose is over; the learner's words are saved
        if word_count(transcript) < MIN_WORDS:
            result = {'insufficient': True}              # not enough evidence is "unknown", never a confirmed zero
        else:
            with engine.begin() as db:
                jobs.heartbeat(db, job, now=now_())
            points = rubric['points']
            raw = assessor.assess(AssessmentInput(title=version['title'], script=[s['text'] for s in version['sentences']],
                                                  question=exercise['prompt'], points=points, transcript=transcript))
            result = {'points': ground_assessment(raw, [p['id'] for p in points], transcript)}
        with engine.begin() as db:
            jobs.assert_current(db, job)
            db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == attempt_id, t.spoken.c.exercise_id == exercise_id))
                       .values(status='assessed', assessment=result, failure_code=None, updated_at=now_()))
            jobs.succeed(db, job, now_())
        return 'done'
    except jobs.LeaseLost:
        return 'lost'
    except Superseded:
        with engine.begin() as db:
            try:
                jobs.succeed(db, job, now_())
            except jobs.LeaseLost:
                return 'lost'
        return 'superseded'
    except ProviderPermanent as error:
        return _fail(engine, objects, job, error.code, now_(), False)
    except ProviderTemporary as error:
        return _fail(engine, objects, job, error.code, now_(), True)
    except ValueError:
        return _fail(engine, objects, job, 'unusable_assessment', now_(), True)      # a model that answers badly once may answer properly next time


def _discard_recording(engine, objects, job, key: str | None, now) -> None:
    if not key:
        return
    objects.delete(key)                                  # idempotent; raising here makes the job retry rather than leak audio
    with engine.begin() as db:
        jobs.assert_current(db, job)
        db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == job.payload['attempt_id'], t.spoken.c.exercise_id == job.payload['exercise_id']))
                   .values(recording_key=None, updated_at=now))


def _fail(engine, objects, job, code: str, now, retryable: bool) -> str:
    """Retry with backoff while tries remain; otherwise settle the answer as failed. Never deletes the learner's saved words."""
    try:
        with engine.begin() as db:
            jobs.assert_current(db, job)
            if retryable and job.attempts < job.max_attempts:
                jobs.retry_later(db, job, code, now)
                return 'retry'
            jobs.fail(db, job, code, now)
            db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == job.payload['attempt_id'], t.spoken.c.exercise_id == job.payload['exercise_id'],
                                                   t.spoken.c.generation == job.payload['generation'], t.spoken.c.status == 'pending'))
                       .values(status='failed', failure_code=code, updated_at=now))
    except jobs.LeaseLost:
        return 'lost'
    _delete_recording_of(engine, objects, job.payload)      # a settled failure must not keep the audio waiting for the sweep
    return 'failed'


def _delete_recording_of(engine, objects, payload: dict) -> None:
    where = and_(t.spoken.c.attempt_id == payload['attempt_id'], t.spoken.c.exercise_id == payload['exercise_id'])
    with engine.connect() as db:
        key = db.execute(select(t.spoken.c.recording_key).where(where)).scalar_one_or_none()
    if not key:
        return
    try:
        objects.delete(key)
    except Exception:
        return                                              # the sweep retries; the key stays recorded until the object is really gone
    with engine.begin() as db:
        db.execute(update(t.spoken).where(and_(where, t.spoken.c.recording_key == key)).values(recording_key=None))


def maintenance(engine, objects, now=None) -> dict:
    """Idempotent housekeeping: settle jobs that ran out of tries, expire abandoned uploads, and delete any recording that outlived its
    purpose. Safe to run from every worker at once."""
    now = now or utcnow()
    counts = {'reaped': 0, 'abandoned': 0, 'deleted': 0}
    with engine.begin() as db:
        for payload in jobs.reap_exhausted(db, now):
            db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == payload['attempt_id'], t.spoken.c.exercise_id == payload['exercise_id'],
                       t.spoken.c.generation == payload['generation'], t.spoken.c.status == 'pending'))
                       .values(status='failed', failure_code='lease_expired', updated_at=now))
            counts['reaped'] += 1
        counts['abandoned'] = db.execute(update(t.spoken).where(and_(t.spoken.c.status == 'receiving', t.spoken.c.updated_at < now - RECEIVING_GRACE))
                                         .values(status='failed', failure_code='upload_incomplete', updated_at=now)).rowcount
        stale = db.execute(select(t.spoken.c.attempt_id, t.spoken.c.exercise_id, t.spoken.c.recording_key, t.spoken.c.generation).where(and_(
            t.spoken.c.recording_key.is_not(None),
            (t.spoken.c.status.in_(('assessed', 'failed', 'skipped')) | (t.spoken.c.updated_at < now - RECORDING_LIFETIME))))).all()
    for row in stale:
        try:
            objects.delete(row.recording_key)
        except Exception:
            continue                                   # leave the key in place; the next sweep tries again
        with engine.begin() as db:
            counts['deleted'] += db.execute(update(t.spoken).where(and_(t.spoken.c.attempt_id == row.attempt_id, t.spoken.c.exercise_id == row.exercise_id,
                t.spoken.c.recording_key == row.recording_key)).values(recording_key=None, updated_at=now)).rowcount
    return counts
