"""Local PostgreSQL proof of concept; no production migrations or activation path.

Lock order: learner profile -> account slot -> operation -> conversation.
Only upload acceptance is implemented; assessment/adaptation remain unchanged.
"""
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
from typing import Literal

from sqlalchemy import (Table, Column, String, Integer, DateTime, ForeignKey,
                        CheckConstraint, MetaData, select, update, text, func)
from sqlalchemy.dialects.postgresql import insert

from app.models import Conversation, Transcription
from app.storage import schema as s
from app.upload_idempotency import attempt_id_for

metadata = MetaData()
slots = Table('poc_account_slots', metadata,
    Column('owner', s.identifier, ForeignKey(s.users.c.id), primary_key=True),
    Column('fence', Integer, nullable=False),
    Column('active', s.identifier),
    CheckConstraint('fence >= 0'))
operations = Table('poc_upload_operations', metadata,
    Column('owner', s.identifier, ForeignKey(s.users.c.id), primary_key=True),
    Column('id', s.identifier, primary_key=True),
    Column('exercise', s.identifier, ForeignKey(s.exercises.c.id), nullable=False),
    Column('upload_key', String(16), nullable=False),
    Column('fingerprint', String(64), nullable=False),
    Column('checkpoint', String(64), nullable=False),
    Column('fence', Integer, nullable=False),
    Column('status', String(16), nullable=False),
    Column('expires', DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('running','complete','cancelled','invalidated')"))


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    kind: Literal['owned', 'processing', 'replay', 'busy']
    operation: str
    fence: int = 0
    result: dict | None = None


def checkpoint(row):
    return sha256(json.dumps(row['data'] if row else None, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def assert_local(engine):
    if (engine.dialect.name != 'postgresql' or engine.url.host not in ('127.0.0.1', 'localhost', '::1')
            or not engine.url.database.startswith('audli_claims_')):
        raise ValueError('Only disposable audli_claims_* databases on loopback are allowed')


def create_schema(engine):
    assert_local(engine)
    metadata.create_all(engine, tables=[slots, operations])


def claims_only_allowed(*, old_workers: int, participating_writers: bool):
    """Deployment precondition, not a mechanism capable of detecting old binaries."""
    if old_workers != 0 or not participating_writers:
        raise Conflict('Claims-only requires drained legacy workers and participating writers')


class Store:
    def __init__(self, repo, *, ttl=60, clock=None):
        assert_local(repo.engine)
        if not 0 < ttl <= 300:
            raise ValueError('TTL outside experiment bounds')
        self.repo, self.owner, self.ttl, self.clock = repo, repo.learner_id, ttl, clock

    def now(self, db):
        return self.clock() if self.clock else db.execute(select(func.clock_timestamp())).scalar_one()

    def locked_slot(self, db):
        # Immutable request-scoped owner. Never derive it from a request payload.
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == self.owner).with_for_update()).one()
        db.execute(insert(slots).values(owner=self.owner, fence=0, active=None).on_conflict_do_nothing())
        return db.execute(select(slots).where(slots.c.owner == self.owner).with_for_update()).mappings().one()

    def owned_state(self, db, exercise):
        self.repo.owned_exercise(db, exercise)
        # Prototype explicitly supports standalone exercise uploads only.
        if db.execute(select(s.lessons.c.id).where(s.lessons.c.user_id == self.owner)).first():
            raise Conflict('Lesson/check-in protocol is outside this prototype')
        if db.execute(select(s.attempts.c.id).where(s.attempts.c.exercise_id == exercise,
                                                   s.attempts.c.status == 'evaluated')).first():
            raise Conflict('Exercise complete')
        current = db.execute(select(s.exercises.c.id).where(s.exercises.c.user_id == self.owner)
                             .order_by(s.exercises.c.created_at.desc()).limit(1)).scalar_one()
        if current != exercise:
            raise Conflict('Exercise changed')
        row = db.execute(select(s.conversations).where(s.conversations.c.exercise_id == exercise)).mappings().first()
        if row is None or row['state'] not in ('AWAITING_SUMMARY', 'AWAITING_FOLLOWUP'):
            raise Conflict('Question not accepting a recording')
        return row

    def result(self, db, exercise, identifier):
        self.repo.owned_exercise(db, exercise)
        row = db.execute(select(s.attempts).where(s.attempts.c.id == identifier,
                                                s.attempts.c.exercise_id == exercise)).mappings().one()
        return {'id': identifier, 'transcription': row['transcription']}

    def claim(self, exercise, upload_key, fingerprint):
        import re
        if not re.fullmatch('[0-9a-f]{16}', upload_key):
            raise Conflict('Stable upload ID required by experiment')
        identifier = attempt_id_for(self.owner, exercise, upload_key)
        with self.repo.transaction() as db:
            slot = self.locked_slot(db)
            self.repo.owned_exercise(db, exercise)
            op = db.execute(select(operations).where(operations.c.owner == self.owner,
                                                    operations.c.id == identifier)).mappings().first()
            if op and op['fingerprint'] != fingerprint:
                raise Conflict('Upload ID reused for different bytes')
            if op and op['status'] == 'complete':
                return Claim('replay', identifier, result=self.result(db, exercise, identifier))
            if op and op['status'] == 'invalidated':
                raise Conflict('Operation invalidated')
            if op is None and db.execute(select(s.attempts.c.id).where(
                    s.attempts.c.id == identifier, s.attempts.c.exercise_id == exercise)).first():
                # A legacy worker can win during bridge overlap. Replay its canonical
                # owner-scoped result without touching the pending checkpoint. Legacy
                # rows have no byte fingerprint; retain the existing stable-ID contract.
                return Claim('replay', identifier, result=self.result(db, exercise, identifier))
            now = self.now(db)
            if slot['active']:
                active = db.execute(select(operations).where(operations.c.owner == self.owner,
                                operations.c.id == slot['active'])).mappings().one()
                if active['status'] == 'running' and active['expires'] > now:
                    return Claim('processing' if active['id'] == identifier else 'busy', identifier)
                db.execute(update(operations).where(operations.c.owner == self.owner,
                    operations.c.id == active['id']).values(status='cancelled'))
            row = self.owned_state(db, exercise)
            if row['pending_attempt_id'] is not None:
                raise Conflict('Existing pending recording must be resolved explicitly')
            stamp = checkpoint(row)
            if op and op['checkpoint'] != stamp:
                raise Conflict('Question changed since first claim')
            fence = slot['fence'] + 1
            db.execute(update(slots).where(slots.c.owner == self.owner).values(fence=fence, active=identifier))
            values = dict(owner=self.owner, id=identifier, exercise=exercise, upload_key=upload_key,
                          fingerprint=fingerprint, checkpoint=stamp, fence=fence, status='running',
                          expires=now + timedelta(seconds=self.ttl))
            db.execute(insert(operations).values(**values).on_conflict_do_update(
                index_elements=['owner', 'id'], set_={key: values[key] for key in ('fence','status','expires')}))
            return Claim('owned', identifier, fence)

    def finalize(self, claim, exercise, transcription: Transcription, *, fail_after_insert=False):
        with self.repo.transaction() as db:
            slot = self.locked_slot(db)
            op = db.execute(select(operations).where(operations.c.owner == self.owner,
                                                    operations.c.id == claim.operation)).mappings().one()
            if op['exercise'] != exercise:
                raise Conflict('Operation scope mismatch')
            if op['status'] == 'complete':
                return self.result(db, exercise, claim.operation)
            if (op['status'] != 'running' or op['fence'] != claim.fence or slot['fence'] != claim.fence
                    or slot['active'] != claim.operation or op['expires'] <= self.now(db)):
                raise Conflict('Expired or superseded claim')
            row = self.owned_state(db, exercise)
            if checkpoint(row) != op['checkpoint']:
                raise Conflict('Question/checkpoint changed')
            db.execute(s.attempts.insert().values(id=claim.operation, exercise_id=exercise,
                transcription=transcription.model_dump(mode='json'), recognized_text=transcription.text,
                confidence=transcription.confidence, transcription_source=transcription.source,
                status='transcribed', created_at=self.now(db)))
            if fail_after_insert:
                raise RuntimeError('injected transaction failure')
            conversation = Conversation.model_validate(row['data'])
            conversation.pending_attempt_id, conversation.pending_text = claim.operation, None
            conversation.state = 'AWAITING_FOLLOWUP' if conversation.active_followup else 'AWAITING_SUMMARY'
            self.repo.write_conversation(db, conversation)
            db.execute(update(operations).where(operations.c.owner == self.owner,
                operations.c.id == claim.operation).values(status='complete'))
            db.execute(update(slots).where(slots.c.owner == self.owner).values(active=None))
            return {'id': claim.operation, 'transcription': transcription.model_dump(mode='json')}

    def abandon(self, claim):
        with self.repo.transaction() as db:
            slot = self.locked_slot(db)
            if slot['fence'] == claim.fence and slot['active'] == claim.operation:
                db.execute(update(operations).where(operations.c.owner == self.owner,
                    operations.c.id == claim.operation, operations.c.status == 'running').values(status='cancelled'))
                db.execute(update(slots).where(slots.c.owner == self.owner).values(active=None))

    def reset(self, exercise):
        """Prototype reset = invalidate active work and clear this question's pending answer."""
        with self.repo.transaction() as db:
            slot = self.locked_slot(db)
            self.repo.owned_exercise(db, exercise)
            if slot['active']:
                db.execute(update(operations).where(operations.c.owner == self.owner,
                    operations.c.id == slot['active']).values(status='invalidated'))
            db.execute(update(slots).where(slots.c.owner == self.owner).values(fence=slot['fence'] + 1, active=None))
            row = db.execute(select(s.conversations).where(s.conversations.c.exercise_id == exercise)).mappings().one()
            conversation = Conversation.model_validate(row['data'])
            conversation.pending_attempt_id = conversation.pending_text = None
            self.repo.write_conversation(db, conversation)

    def status(self, exercise, upload_key):
        identifier = attempt_id_for(self.owner, exercise, upload_key)
        with self.repo.read() as db:
            self.repo.owned_exercise(db, exercise)
            op = db.execute(select(operations).where(operations.c.owner == self.owner,
                operations.c.id == identifier, operations.c.exercise == exercise)).mappings().first()
            if op and op['status'] == 'complete':
                return self.result(db, exercise, identifier)
            if op and op['status'] == 'running' and op['expires'] > self.now(db):
                return None
            raise Conflict('No live operation')
