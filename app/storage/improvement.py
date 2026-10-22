"""Owned persistence for advisory Review coaching. Short atomic transactions only: the claim and the completion
never span a provider call, and no account-wide lock is taken. Reads write nothing and consume no quota."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update, and_, case, func
from sqlalchemy.exc import IntegrityError

from app.improvement import ImprovementState, Note, view, source_hash, eligibility, available_state, RETRY_CAP
from app.storage import schema as s
from uuid import uuid4

LIVE = ('pending', 'ready', 'rejected')  # The statuses that make up an exercise's CURRENT result (the latest of them wins).
COUNTED = ('pending', 'ready', 'rejected')  # Failed and abandoned claims never count toward the learner's allowance.
WINDOW = timedelta(hours=24)
CLAIM_LEASE = timedelta(seconds=90)         # A pending claim older than this was abandoned (crash, disconnect, deploy).
FAILURE_COOLDOWN = timedelta(seconds=30)    # Retrying the same exercise right after a provider failure.
FAILURE_WINDOW = timedelta(hours=1)
FAILURE_LIMIT = 5                           # Failed+abandoned claims per learner per hour, so outages stay bounded too.


@dataclass
class Claim:
    kind: str                       # existing | final | pending | claimed | limit | cooldown | burst
    row: dict | None = None
    retry_after: int | None = None
    id: str | None = None
    retry_available: bool = False   # For a stored rejected result: another attempt is permitted.


def recency(table):
    """Ordering for 'the latest result': by claim time, and at the same instant a suggestion or an attempt in progress
    outranks a rejection, so the answer never depends on a random identifier."""
    rank = case((table.c.status == 'ready', 2), (table.c.status == 'pending', 1), else_=0)
    return (table.c.claimed_at, rank, table.c.id)


def rejected_count(db, owner, exercise_id, prompt_version):
    table = s.review_improvements
    return db.execute(select(func.count()).select_from(table).where(and_(table.c.user_id == owner, table.c.exercise_id == exercise_id,
        table.c.status == 'rejected', table.c.prompt_version == prompt_version))).scalar_one()


def current_row(db, owner, exercise_id):
    """The latest pending, ready or rejected row: an exercise may hold older rejected attempts and a later result."""
    table = s.review_improvements
    row = db.execute(select(table).where(and_(table.c.exercise_id == exercise_id, table.c.user_id == owner, table.c.status.in_(LIVE)))
        .order_by(*[column.desc() for column in recency(table)]).limit(1)).mappings().first()
    return dict(row) if row else None


def _aware(value):
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def _seconds(delta: timedelta) -> int:
    return max(1, int(delta.total_seconds()) + 1)


def evaluated_root(repo, exercise_id):
    """The evaluated attempt row for an exercise this learner owns, or None."""
    with repo.engine.connect() as db:
        row = db.execute(select(s.attempts.c.id.label('root_attempt'), s.attempts.c.transcription.label('root_transcription'),
            s.attempts.c.confirmed_text.label('root_text')).select_from(s.attempts.join(s.exercises,
            s.exercises.c.id == s.attempts.c.exercise_id)).where(and_(s.attempts.c.exercise_id == exercise_id,
            s.attempts.c.status == 'evaluated', s.exercises.c.user_id == repo.learner_id))).mappings().first()
        return dict(row) if row else None


def claim(repo, exercise_id: str, attempt_id: str, text: str, prompt_version: str, daily_limit: int, at: datetime, retry: bool = False) -> Claim:
    """Atomically return the current result, join a fresh claim, or take a new one if the allowance permits.

    A rejected result is shown again for free. Asking again (`retry`) is a NEW attempt: it passes through the same
    cooldown, burst and rolling-allowance checks as any other, and is allowed once per exercise per prompt version
    (RETRY_CAP rejected results in total). A rejection recorded under an older prompt version does not count."""
    digest = source_hash(text)
    try:
        with repo.transaction() as db:
            # Serializes this learner's claims across workers for the few milliseconds of this transaction only.
            db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == repo.learner_id).with_for_update())
            owned = db.execute(select(s.exercises.c.id).where(and_(s.exercises.c.id == exercise_id,
                s.exercises.c.user_id == repo.learner_id))).first()
            if owned is None:
                raise LookupError('Exercise not found.')
            table = s.review_improvements
            live = current_row(db, repo.learner_id, exercise_id)
            if live is not None:
                if live['status'] == 'ready':
                    return Claim('existing', live)
                if live['status'] == 'rejected':
                    allowed = rejected_count(db, repo.learner_id, exercise_id, prompt_version) < RETRY_CAP
                    if not retry:
                        return Claim('existing', live, retry_available=allowed)
                    if not allowed:
                        return Claim('final', live)
                elif at - _aware(live['claimed_at']) < CLAIM_LEASE:
                    return Claim('pending', live, id=live['id'])
                else:
                    db.execute(update(table).where(and_(table.c.id == live['id'], table.c.status == 'pending'))
                        .values(status='abandoned', finished_at=at))
            recent_failure = db.execute(select(func.max(table.c.finished_at)).where(and_(table.c.exercise_id == exercise_id,
                table.c.user_id == repo.learner_id, table.c.status == 'failed'))).scalar_one()
            if recent_failure is not None and at - _aware(recent_failure) < FAILURE_COOLDOWN:
                return Claim('cooldown', retry_after=_seconds(FAILURE_COOLDOWN - (at - _aware(recent_failure))))
            failures = db.execute(select(table.c.claimed_at).where(and_(table.c.user_id == repo.learner_id,
                table.c.status.in_(('failed', 'abandoned')), table.c.claimed_at > at - FAILURE_WINDOW))
                .order_by(table.c.claimed_at)).scalars().all()
            if len(failures) >= FAILURE_LIMIT:
                return Claim('burst', retry_after=_seconds(_aware(failures[len(failures) - FAILURE_LIMIT]) + FAILURE_WINDOW - at))
            rows = db.execute(select(table.c.claimed_at, table.c.status).where(and_(table.c.user_id == repo.learner_id,
                table.c.status.in_(COUNTED), table.c.claimed_at > at - WINDOW)).order_by(table.c.claimed_at)).all()
            # A pending claim that outlived its lease was abandoned, so it must not hold an allowance slot.
            counted = [claimed for claimed, status in rows if status != 'pending' or at - _aware(claimed) < CLAIM_LEASE]
            if len(counted) >= daily_limit:
                return Claim('limit', retry_after=_seconds(_aware(counted[len(counted) - daily_limit]) + WINDOW - at))
            identifier = str(uuid4())
            db.execute(table.insert().values(id=identifier, user_id=repo.learner_id, exercise_id=exercise_id,
                attempt_id=attempt_id, status='pending', source_hash=digest, prompt_version=prompt_version, claimed_at=at))
            return Claim('claimed', id=identifier)
    except IntegrityError:
        # A concurrent claim won the one-active-row-per-exercise index; join whatever it produced.
        with repo.engine.connect() as db:
            live = current_row(db, repo.learner_id, exercise_id)
        if live is None:
            raise
        return Claim('existing' if live['status'] != 'pending' else 'pending', live, id=live['id'])


def finish(repo, claim_id: str, at: datetime, status: str, *, improved_text=None, notes=None, rejection=None, model=None, repaired=False) -> bool:
    """Compare-and-set completion: only a still-pending claim owned by this learner can be completed."""
    table = s.review_improvements
    with repo.transaction() as db:
        result = db.execute(update(table).where(and_(table.c.id == claim_id, table.c.user_id == repo.learner_id,
            table.c.status == 'pending')).values(status=status, finished_at=at, improved_text=improved_text,
            notes=notes if status == 'ready' else None, rejection=rejection, model=model, repaired=repaired))
        return result.rowcount == 1


def live_row(repo, exercise_id):
    with repo.engine.connect() as db:
        return current_row(db, repo.learner_id, exercise_id)


def can_retry(repo, exercise_id: str, prompt_version: str) -> bool:
    with repo.engine.connect() as db:
        return rejected_count(db, repo.learner_id, exercise_id, prompt_version) < RETRY_CAP


def live_rows(repo, db, exercise_ids: list[str], prompt_version: str) -> dict[str, dict]:
    """Owner-filtered batched read for a whole report: each exercise's current row plus whether a retry is permitted."""
    if not exercise_ids:
        return {}
    table = s.review_improvements
    rows = db.execute(select(table).where(and_(table.c.user_id == repo.learner_id, table.c.exercise_id.in_(exercise_ids),
        table.c.status.in_(LIVE))).order_by(*recency(table))).mappings().all()
    current = {row['exercise_id']: dict(row) for row in rows}      # The latest claim per exercise wins (ordered ascending).
    rejected = Counter(row['exercise_id'] for row in rows if row['status'] == 'rejected' and row['prompt_version'] == prompt_version)
    for exercise_id, row in current.items():
        row['retry_available'] = row['status'] == 'rejected' and rejected[exercise_id] < RETRY_CAP
    return current


def state_from_row(row: dict, source_text: str, at: datetime, retry_available: bool | None = None) -> ImprovementState | None:
    """The learner-facing state for a stored row, or None when it no longer matches the saved answer."""
    if source_hash(source_text) != row['source_hash']:
        return None
    if row['status'] == 'ready':
        return view(source_text, row['improved_text'], [Note.model_validate(note) for note in row['notes'] or []])
    if row['status'] == 'rejected':
        allowed = row.get('retry_available', False) if retry_available is None else retry_available
        return ImprovementState(state='rejected', reason=row['rejection'], retry_available=allowed)
    if row['status'] == 'pending':
        if at - _aware(row['claimed_at']) < CLAIM_LEASE:
            return ImprovementState(state='pending')
        return ImprovementState(state='available')
    return None


def first_answer(repo, exercise_id: str) -> dict | None:
    """The learner's first saved response (the summary) for an evaluated, owned exercise."""
    from app.storage.review import attach_answers
    root = evaluated_root(repo, exercise_id)
    if root is None:
        return None
    row = {'exercise_id': exercise_id, **root}
    with repo.engine.connect() as db:
        attach_answers(repo, db, [row])
    return row['answers'][0] if row['answers'] else None


def report_state(row: dict, minimum_confidence: float, at: datetime) -> ImprovementState | None:
    """Review's view of one passage: a stored result if there is one, otherwise whether coaching can be requested."""
    answers = row.get('answers') or []
    if not answers:
        return None
    answer = answers[0]
    live = row.get('improvement_row')
    if live is not None:
        state = state_from_row(live, answer['text'], at)
        if state is not None:
            return state
    _, reason = eligibility(answer['text'], answer.get('transcription'), answer['text'], minimum_confidence)
    return available_state(reason)
