"""Durable admission, not a claim about provider billing. No positive flag cache."""
import asyncio
import hashlib
import logging
from time import monotonic
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select, update, func
from sqlalchemy.exc import SQLAlchemyError
from app.storage import schema as s
from app.storage.repository import utcnow, aware

# A timed-out HTTP call may still run remotely. Hold its slot until the lease ends.
LEASE_SECONDS = 90
CONTROL_TIMEOUT_SECONDS = 2


class SharedAccentControls:
    def __init__(self, engine, settings):
        self.settings = settings
        self.owns_engine = engine.dialect.name == 'postgresql'
        # Separate, bounded pool: unavailable accent state cannot monopolize lesson DB connections.
        self.engine = create_engine(engine.url, pool_pre_ping=True, pool_size=2, max_overflow=0,
            pool_timeout=.2, connect_args={'connect_timeout': 1, 'prepare_threshold': None,
            'options': '-c timezone=UTC -c statement_timeout=750 -c lock_timeout=250 -c idle_in_transaction_session_timeout=2000'}) if self.owns_engine else engine

    def close(self):
        if self.owns_engine:
            self.engine.dispose()

    @contextmanager
    def transaction(self):
        with self.engine.connect() as db:
            if db.dialect.name == 'sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                db.begin()
            try:
                if db.dialect.name == 'postgresql':
                    # Session poolers may discard libpq startup options. Set limits
                    # locally in every transaction, including reads and rechecks.
                    db.exec_driver_sql("SELECT set_config('statement_timeout','750ms',true), "
                        "set_config('lock_timeout','250ms',true), "
                        "set_config('idle_in_transaction_session_timeout','2000ms',true), "
                        "set_config('TimeZone','UTC',true)")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def clock(self, db):
        return aware(db.execute(select(func.clock_timestamp())).scalar_one()) if db.dialect.name == 'postgresql' else utcnow()

    async def call(self, function, *args):
        started = monotonic()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(function, *args), CONTROL_TIMEOUT_SECONDS)
            # A busy event loop can delay timeout callbacks. Never accept a positive
            # DB result whose observed age exceeded the deadline, even in that case.
            return result if monotonic() - started <= CONTROL_TIMEOUT_SECONDS else None
        except (SQLAlchemyError, OSError, TimeoutError, ValueError):
            logging.getLogger('audli').warning('Accent control outcome=unavailable')
            return None

    def read(self):
        with self.transaction() as db:
            return dict(db.execute(select(s.accent_control).where(s.accent_control.c.id == 1)).mappings().one())

    async def voices(self):
        if not self.settings.accent_practice_enabled:
            return {}
        row = await self.call(self.read)
        return self.settings.available_passage_voices if row and row['enabled'] else {}

    def configure(self, *, enabled, daily_limit=None, concurrent_limit=None):
        """Operator-only method; never registered as an HTTP endpoint. No budget reset."""
        if daily_limit is not None and (type(daily_limit) is not int or daily_limit < 0):
            raise ValueError('Invalid daily limit')
        if concurrent_limit is not None and (type(concurrent_limit) is not int or not 1 <= concurrent_limit <= 16):
            raise ValueError('Invalid concurrency limit')
        with self.transaction() as db:
            row = db.execute(select(s.accent_control).where(s.accent_control.c.id == 1).with_for_update()).mappings().one()
            limit = row['daily_limit'] if daily_limit is None else daily_limit
            if enabled and limit <= 0:
                raise ValueError('Positive shared budget required')
            values = dict(enabled=int(enabled), revision=row['revision']+1, updated_at=self.clock(db))
            if daily_limit is not None:
                values['daily_limit'] = daily_limit
            if concurrent_limit is not None:
                values['concurrent_limit'] = concurrent_limit
            db.execute(update(s.accent_control).where(s.accent_control.c.id == 1).values(**values))
            return row['revision']+1

    def reserve(self, owner, lesson_id, exercise_number, accent, count):
        from app.lesson import Lesson
        from app.accent_practice import authorized_accent
        if (not self.settings.accent_practice_enabled or accent not in self.settings.available_passage_voices
                or not 1 <= count <= self.settings.passage_tts_max_characters):
            return None
        # Stable across retries/restarts and private: no learner IDs in usage logs/ledger.
        scope = hashlib.sha256(f'{owner}:{lesson_id}:{exercise_number}'.encode()).hexdigest()
        with self.transaction() as db:
            control = db.execute(select(s.accent_control).where(s.accent_control.c.id == 1).with_for_update()).mappings().one()
            at = self.clock(db)
            if not control['enabled']:
                return None
            checkpoint = db.execute(select(s.lessons.c.data).where((s.lessons.c.id == lesson_id)
                & (s.lessons.c.user_id == owner)).with_for_update()).scalar_one_or_none()
            if checkpoint is None:
                return None
            lesson = Lesson.model_validate(checkpoint)
            if lesson.phase != 'EXERCISE' or lesson.exercise_id or lesson.exercises_completed != exercise_number or authorized_accent(lesson, at) != accent:
                return None
            if db.execute(select(s.accent_reservations.c.scope).where(s.accent_reservations.c.scope == scope)).first():
                return None
            day = at.date().isoformat()
            used = control['characters'] if control['budget_day'] == day else 0
            # Both the durable fleet limit and deployment's local ceiling apply.
            if used + count > min(control['daily_limit'], self.settings.passage_tts_daily_characters):
                return None
            active = db.execute(select(func.count()).select_from(s.accent_reservations)
                .where(s.accent_reservations.c.lease_until > at)).scalar_one()
            if active >= min(control['concurrent_limit'], self.settings.passage_tts_max_concurrent):
                return None
            db.execute(update(s.accent_reservations).where((s.accent_reservations.c.state == 'active')
                & (s.accent_reservations.c.lease_until <= at)).values(state='abandoned'))
            token = str(uuid4())
            db.execute(s.accent_reservations.insert().values(scope=scope, token=token, budget_day=day,
                characters=count, flag_revision=control['revision'], state='active', created_at=at,
                lease_until=at+timedelta(seconds=LEASE_SECONDS)))
            db.execute(update(s.accent_control).where(s.accent_control.c.id == 1).values(budget_day=day, characters=used+count))
            return token, control['revision']

    def reserve_default(self, count):
        """Admit one ordinary-passage synthesis against the same fleet budget/concurrency as accents.

        Not tied to accent consent or the accent kill switch. Returns (token, revision) or a fixed reason."""
        if not 1 <= count <= self.settings.passage_tts_max_characters:
            return 'too_long'
        with self.transaction() as db:
            control = db.execute(select(s.accent_control).where(s.accent_control.c.id == 1).with_for_update()).mappings().one()
            at = self.clock(db)
            day = at.date().isoformat()
            used = control['characters'] if control['budget_day'] == day else 0
            if used + count > min(control['daily_limit'], self.settings.passage_tts_daily_characters):
                return 'budget_exhausted'
            active = db.execute(select(func.count()).select_from(s.accent_reservations)
                .where(s.accent_reservations.c.lease_until > at)).scalar_one()
            if active >= min(control['concurrent_limit'], self.settings.passage_tts_max_concurrent):
                return 'concurrency_limit'
            db.execute(update(s.accent_reservations).where((s.accent_reservations.c.state == 'active')
                & (s.accent_reservations.c.lease_until <= at)).values(state='abandoned'))
            token = str(uuid4())
            db.execute(s.accent_reservations.insert().values(scope=hashlib.sha256(f'default:{token}'.encode()).hexdigest(),
                token=token, budget_day=day, characters=count, flag_revision=control['revision'], state='active',
                created_at=at, lease_until=at+timedelta(seconds=LEASE_SECONDS)))
            db.execute(update(s.accent_control).where(s.accent_control.c.id == 1).values(budget_day=day, characters=used+count))
            return token, control['revision']

    def finish(self, token, state):
        with self.transaction() as db:
            # Same lock order as admission/disable. Characters are never refunded.
            db.execute(select(s.accent_control.c.id).where(s.accent_control.c.id == 1).with_for_update()).one()
            values = {'state': state}
            if state == 'success':
                values['lease_until'] = self.clock(db)
            db.execute(update(s.accent_reservations).where((s.accent_reservations.c.token == token)
                & (s.accent_reservations.c.state == 'active')).values(**values))

    def check_reservation(self, reservation, owner=None, lesson_id=None, number=None, accent=None):
        from app.lesson import Lesson
        from app.accent_practice import authorized_accent
        with self.transaction() as db:
            row = db.execute(select(s.accent_control.c.enabled, s.accent_control.c.revision)
                .where(s.accent_control.c.id == 1)).mappings().one()
            lease = db.execute(select(s.accent_reservations).where(
                (s.accent_reservations.c.token == reservation[0])
                & (s.accent_reservations.c.state == 'active'))).mappings().first()
            checkpoint = None
            if owner is not None:
                checkpoint = db.execute(select(s.lessons.c.data).where((s.lessons.c.id == lesson_id)
                    & (s.lessons.c.user_id == owner))).scalar_one_or_none()
            at = self.clock(db)
            if not (row['enabled'] and row['revision'] == reservation[1] and lease
                    and aware(lease['lease_until']) > at):
                return False
            if owner is not None:
                if checkpoint is None:
                    return False
                lesson = Lesson.model_validate(checkpoint)
                scope = hashlib.sha256(f'{owner}:{lesson_id}:{number}'.encode()).hexdigest()
                return bool(lease['scope'] == scope and lesson.phase == 'EXERCISE'
                    and not lesson.exercise_id and lesson.exercises_completed == number
                    and authorized_accent(lesson, at) == accent)
            return True

    async def valid(self, reservation, *, owner=None, lesson=None, accent=None):
        return bool(self.settings.accent_practice_enabled and await self.call(self.check_reservation,
            reservation, owner, lesson.id if lesson else None,
            lesson.exercises_completed if lesson else None, accent))
