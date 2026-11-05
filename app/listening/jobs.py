"""Durable job queue in the application database (no Redis, no extra infrastructure).

A worker *claims* a job, which grants a lease and bumps a fencing token. Everything the worker writes afterwards is conditional on
that token being the latest one (``assert_current``), inside the same transaction as the result. If a worker stalls past its lease
and another worker takes the job over, the first worker's later writes find a newer fence and are refused, so a slow or zombie
worker can never overwrite the result of the one that replaced it. Enqueueing is idempotent through ``dedupe_key``.

Claims use one atomic UPDATE; on PostgreSQL the candidate row is picked with ``FOR UPDATE SKIP LOCKED`` so concurrent workers never
wait for, or double-claim, the same job.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update, insert
from sqlalchemy.exc import IntegrityError

from app.listening import schema as t
from app.storage.repository import utcnow

DEFAULT_LEASE_SECONDS = 120
DEFAULT_MAX_ATTEMPTS = 4


class LeaseLost(Exception):
    """This worker no longer holds the job: it expired and was taken over, or finished elsewhere."""


@dataclass(frozen=True)
class Job:
    id: str
    user_id: str
    kind: str
    payload: dict
    attempts: int
    max_attempts: int
    fence: int
    owner: str


def backoff_seconds(attempts: int) -> int:
    """Delay before the next try after `attempts` tries have been made: 15 s, 30 s, 60 s ... capped at 10 minutes."""
    return min(600, 15 * 2 ** max(0, attempts - 1))


def enqueue(db, user_id: str, kind: str, dedupe_key: str, payload: dict, max_attempts: int = DEFAULT_MAX_ATTEMPTS, now: datetime | None = None) -> str:
    """Idempotent: the same dedupe_key returns the existing job instead of adding another."""
    existing = db.execute(select(t.jobs.c.id).where(t.jobs.c.dedupe_key == dedupe_key)).scalar_one_or_none()
    if existing:
        return existing
    now = now or utcnow()
    job_id = str(uuid4())
    try:
        with db.begin_nested():
            db.execute(insert(t.jobs).values(id=job_id, user_id=user_id, kind=kind, dedupe_key=dedupe_key, payload=payload, status='queued',
                attempts=0, max_attempts=max_attempts, run_after=now, lease_owner=None, lease_expires_at=None, fence=0, last_error=None,
                created_at=now, updated_at=now, finished_at=None))
    except IntegrityError:          # a concurrent identical enqueue won the unique key
        return db.execute(select(t.jobs.c.id).where(t.jobs.c.dedupe_key == dedupe_key)).scalar_one()
    return job_id


def claim(db, owner: str, lease_seconds: int = DEFAULT_LEASE_SECONDS, now: datetime | None = None, kind: 'str | tuple[str, ...] | None' = None) -> Job | None:
    now = now or utcnow()
    eligible = or_(and_(t.jobs.c.status == 'queued', t.jobs.c.run_after <= now),
                   and_(t.jobs.c.status == 'running', t.jobs.c.lease_expires_at < now, t.jobs.c.attempts < t.jobs.c.max_attempts))
    candidate = select(t.jobs.c.id).where(eligible).order_by(t.jobs.c.run_after, t.jobs.c.created_at).limit(1)
    if kind:
        candidate = candidate.where(t.jobs.c.kind.in_((kind,) if isinstance(kind, str) else tuple(kind)))
    if db.dialect.name == 'postgresql':
        candidate = candidate.with_for_update(skip_locked=True)
    row = db.execute(update(t.jobs).where(t.jobs.c.id == candidate.scalar_subquery()).values(
        status='running', lease_owner=owner, lease_expires_at=now + timedelta(seconds=lease_seconds),
        fence=t.jobs.c.fence + 1, attempts=t.jobs.c.attempts + 1, updated_at=now)
        .returning(t.jobs.c.id, t.jobs.c.user_id, t.jobs.c.kind, t.jobs.c.payload, t.jobs.c.attempts, t.jobs.c.max_attempts, t.jobs.c.fence)).first()
    if row is None:
        return None
    return Job(id=row.id, user_id=row.user_id, kind=row.kind, payload=row.payload, attempts=row.attempts, max_attempts=row.max_attempts,
               fence=row.fence, owner=owner)


def assert_current(db, job: Job) -> None:
    """Lock the job row and prove this worker still holds the latest fence. Call inside the transaction that writes the result."""
    query = select(t.jobs.c.id).where(and_(t.jobs.c.id == job.id, t.jobs.c.fence == job.fence, t.jobs.c.lease_owner == job.owner,
                                           t.jobs.c.status == 'running'))
    if db.execute(query.with_for_update() if db.dialect.name == 'postgresql' else query).first() is None:
        raise LeaseLost(job.id)


def _fenced(job: Job):
    return and_(t.jobs.c.id == job.id, t.jobs.c.fence == job.fence, t.jobs.c.lease_owner == job.owner, t.jobs.c.status == 'running')


def heartbeat(db, job: Job, lease_seconds: int = DEFAULT_LEASE_SECONDS, now: datetime | None = None) -> None:
    now = now or utcnow()
    if db.execute(update(t.jobs).where(_fenced(job)).values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)).rowcount != 1:
        raise LeaseLost(job.id)


def succeed(db, job: Job, now: datetime | None = None) -> None:
    now = now or utcnow()
    if db.execute(update(t.jobs).where(_fenced(job)).values(status='succeeded', lease_owner=None, lease_expires_at=None, last_error=None,
                                                            updated_at=now, finished_at=now)).rowcount != 1:
        raise LeaseLost(job.id)


def retry_later(db, job: Job, code: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    if db.execute(update(t.jobs).where(_fenced(job)).values(status='queued', lease_owner=None, lease_expires_at=None, last_error=code[:60],
            run_after=now + timedelta(seconds=backoff_seconds(job.attempts)), updated_at=now)).rowcount != 1:
        raise LeaseLost(job.id)


def fail(db, job: Job, code: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    if db.execute(update(t.jobs).where(_fenced(job)).values(status='failed', lease_owner=None, lease_expires_at=None, last_error=code[:60],
                                                            updated_at=now, finished_at=now)).rowcount != 1:
        raise LeaseLost(job.id)


def reap_exhausted(db, now: datetime | None = None, kinds: tuple[str, ...] | None = None) -> list[dict]:
    """Jobs whose lease expired after their last allowed try can never be claimed again; fail them and return their payloads so the
    caller can settle the work they belonged to. `kinds` limits the sweep to the caller's own job kinds."""
    now = now or utcnow()
    query = select(t.jobs.c.id, t.jobs.c.payload).where(and_(t.jobs.c.status == 'running', t.jobs.c.lease_expires_at < now,
                                                             t.jobs.c.attempts >= t.jobs.c.max_attempts))
    if kinds:                                       # each process settles only its own kinds: a payload of another kind means nothing to it
        query = query.where(t.jobs.c.kind.in_(kinds))
    rows = db.execute(query).all()
    reaped = []
    for row in rows:
        if db.execute(update(t.jobs).where(and_(t.jobs.c.id == row.id, t.jobs.c.status == 'running', t.jobs.c.lease_expires_at < now))
                      .values(status='failed', lease_owner=None, lease_expires_at=None, last_error='lease_expired', updated_at=now,
                              finished_at=now)).rowcount == 1:
            reaped.append(row.payload)
    return reaped
