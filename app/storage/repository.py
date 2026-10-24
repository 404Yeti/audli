"""Storage-independent operations shared by the SQLite and Postgres adapters."""
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, update, and_, text, exists, event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from app.models import LearnerProfile, Conversation, TrainingSession, Transcription
from app.storage import schema as s
from app.lesson_limit import WINDOW, LessonCooldown, GRACE_ACTION_LIMIT, GRACE_SECONDS

LOG = logging.getLogger('uvicorn.error')


def learner_tag(learner_id):
    """Stable, non-reversible log label; learner IDs are not logged."""
    return sha256(str(learner_id).encode()).hexdigest()[:12]


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def profile_values(profile):
    return dict(data=profile.model_dump(mode='json'), name=profile.name,
        target_language=profile.target_language, goal=profile.goal, interests=profile.interests,
        target_situations=profile.target_situations, onboarding_status=profile.onboarding_status,
        initial_listening_profile=profile.initial_listening_profile.model_dump() if profile.initial_listening_profile else None,
        **profile.listening_profile.model_dump(), **profile.difficulty.model_dump(),
        focus=profile.focus, completed_attempts=profile.completed_attempts, updated_at=utcnow())


def evaluation_values(evaluation):
    data = evaluation.model_dump(mode='json')
    return dict(data=data, version=data.get('version', 'v0.1'),
        **{key: data.get(key) for key in ('overall', 'main_idea', 'details', 'vocabulary', 'inference', 'coverage')},
        coaching_outcome=data['feedback'], created_at=utcnow())


def event_values(event):
    return dict(data=event.model_dump(mode='json'),
        **{key: getattr(event, key) for key in ('decision', 'changed_variable', 'old_value', 'new_value',
            'reason', 'previous_score', 'policy_version')}, created_at=utcnow())


def api_row(row):
    if row is None:
        return None
    result = dict(row)
    for key in ('content', 'difficulty', 'transcription'):
        if key in result:
            result[key] = json.dumps(result[key])
    if isinstance(result.get('created_at'), datetime):
        result['created_at'] = aware(result['created_at']).isoformat()
    return result


LOCKING_READS = (' FOR UPDATE', ' FOR SHARE', ' FOR NO KEY UPDATE', ' FOR KEY SHARE', 'PG_ADVISORY', 'SET_CONFIG', 'NEXTVAL')


def guard_reads(engine):
    """Reject anything but a plain SELECT on a connection opened by ``SQLProgressRepository.read``. Installed once per engine."""
    if getattr(engine, '_audli_read_guard', False):
        return
    engine._audli_read_guard = True

    @event.listens_for(engine, 'before_cursor_execute')
    def check(conn, cursor, statement, parameters, context, executemany):
        if conn.get_execution_options().get('audli_read_only'):
            head = statement.lstrip().upper()
            if not head.startswith(('SELECT', 'WITH', 'SHOW')) or any(token in head for token in LOCKING_READS):
                raise RuntimeError('A read-only database connection was used for a write or a locking statement')


class AccountUnavailable(Exception):
    """The Auth account no longer exists, so no application rows may be created for it."""


class SQLProgressRepository:
    """Application repository contract; adapters supply an engine and learner scope.

    Domain snapshots keep existing API formats. Relational columns/projections are
    written in the same transaction and support history, ownership and future policy.
    """
    limit_mode = 'off'  # AUD-47; set from settings by create_app and inherited by every request scope.
    require_auth_user = False  # AUD-34: initialization must find the live Auth user (set by create_app when deletion is enabled).

    def __init__(self, engine, learner_id: str | None):
        self.engine = engine
        self.learner_id = learner_id

    @property
    def wall_clock(self):
        return self.limit_mode == 'enforce'

    # AUD-84 P2: read-only operations may run in AUTOCOMMIT on Postgres (no BEGIN, no closing rollback round trip).
    read_autocommit = False
    read_guard = False          # tests switch this on everywhere so a mislabelled write cannot hide on SQLite

    @contextmanager
    def read(self):
        """A connection for a read-only operation (plain SELECTs only).

        Normally identical to ``engine.connect()``. With ``read_autocommit`` (Postgres only) the connection runs in
        AUTOCOMMIT: every statement is its own implicit transaction, so the driver sends no ``BEGIN`` and no ``ROLLBACK``
        when the connection goes back to the pool (two network round trips per operation). Postgres runs READ COMMITTED
        here, where each statement already takes its own snapshot, so a multi-statement read sees exactly what it saw
        inside a transaction (the startup check below refuses to enable this on any other isolation level). Writes and
        locking reads never use this method; ``guard_reads`` rejects them if they ever do.
        """
        guarded = self.read_autocommit or self.read_guard
        with self.engine.connect() as db:
            if guarded:
                guard_reads(self.engine)
                db.execution_options(audli_read_only=True, **({'isolation_level': 'AUTOCOMMIT'} if self.read_autocommit else {}))
            yield db

    def autocommit_reads_allowed(self) -> tuple[bool, str]:
        """Whether AUTOCOMMIT reads are safe on this database; the reason when they are not."""
        if self.engine.dialect.name != 'postgresql':
            return False, 'not PostgreSQL'
        with self.engine.connect() as db:
            level = db.execute(text('SHOW default_transaction_isolation')).scalar_one()
        return level == 'read committed', f'default_transaction_isolation is {level!r}'

    def for_learner(self, learner_id: str):
        """A new request scope; never mutate the pool owner's identity or dispose it."""
        scoped = SQLProgressRepository(self.engine, learner_id)
        scoped.limit_mode = self.limit_mode
        scoped.require_auth_user = self.require_auth_user
        scoped.read_autocommit = self.read_autocommit
        # Auth has already verified this identity. Existing accounts need a read,
        # not two no-op upserts and a commit on every protected request.
        with self.read() as db:
            initialized = db.execute(select(s.profiles.c.user_id).join(s.users,
                s.users.c.id == s.profiles.c.user_id).where(s.profiles.c.user_id == learner_id)).first()
        if initialized is None:
            scoped.initialize_learner()
        return scoped

    def close(self):
        self.engine.dispose()

    @contextmanager
    def transaction(self, *, planner=False):
        with self.engine.connect() as db:
            # SQLite needs the write lock before reading; Postgres uses row locks.
            if self.engine.dialect.name == 'sqlite':
                if planner:
                    db.exec_driver_sql('PRAGMA busy_timeout=250')
                try:
                    db.exec_driver_sql('BEGIN IMMEDIATE')
                except Exception:
                    if planner:
                        db.exec_driver_sql('PRAGMA busy_timeout=20000')
                    raise
            else:
                db.begin()
            try:
                if planner and self.engine.dialect.name == 'postgresql':
                    db.exec_driver_sql("SET LOCAL lock_timeout='250ms'")
                    db.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                if planner and self.engine.dialect.name == 'sqlite':
                    db.exec_driver_sql('PRAGMA busy_timeout=20000')

    def upsert(self, db, table, values, keys):
        insert = postgres_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        statement = insert(table).values(**values)
        db.execute(statement.on_conflict_do_update(index_elements=keys,
            set_={key: getattr(statement.excluded, key) for key in values if key not in keys}))

    def initialize_learner(self):
        insert = postgres_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        with self.transaction() as db:
            if self.require_auth_user:
                # A share lock on the Auth row serializes with account deletion (which locks it FOR UPDATE first): a request that
                # verified its token just before deletion cannot recreate the learner after the erase committed.
                if not db.execute(text('SELECT id FROM auth.users WHERE id = :owner FOR SHARE'), {'owner': self.learner_id}).first():
                    raise AccountUnavailable()
            db.execute(insert(s.users).values(id=self.learner_id, created_at=utcnow()).on_conflict_do_nothing())
            db.execute(insert(s.profiles).values(user_id=self.learner_id,
                **profile_values(LearnerProfile())).on_conflict_do_nothing())

    def profile(self):
        with self.read() as db:
            row = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id)).mappings().one()
            return LearnerProfile(tutor_voice=row['data'].get('tutor_voice', 'original'), accent_preferences=row['data'].get('accent_preferences', {}), onboarding=row['data'].get('onboarding', {}), name=row['name'], target_language=row['target_language'], goal=row['goal'],
                interests=row['interests'], target_situations=row['target_situations'],
                onboarding_status=row['onboarding_status'], initial_listening_profile=row['initial_listening_profile'],
                listening_profile={key: row[key] for key in ('overall', 'main_idea', 'details', 'vocabulary', 'inference', 'natural_speed')},
                difficulty={key: row[key] for key in ('speech_rate', 'duration_seconds', 'vocabulary_level', 'information_density',
                    'speaker_count', 'accent_complexity', 'background_noise')}, focus=row['focus'], completed_attempts=row['completed_attempts'])

    def save_profile(self, profile: LearnerProfile):
        with self.transaction() as db:
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id).values(**profile_values(profile)))

    def save_tutor_voice(self, voice):
        """Change only this owner's voice against the latest locked snapshot."""
        with self.transaction() as db:
            row = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .with_for_update()).mappings().one()
            current = LearnerProfile.model_validate(row['data'])
            current.tutor_voice = voice
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .values(**profile_values(current)))
        return current.tutor_voice

    def save_accent_preferences(self, preferences):
        """Update only this owner's preference against current, locked learner state."""
        with self.transaction() as db:
            row = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .with_for_update()).mappings().one()
            current = LearnerProfile.model_validate(row['data'])
            current.accent_preferences = preferences
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .values(**profile_values(current)))
        return current.accent_preferences

    def save_onboarding(self, profile, expected_revision):
        """Compare and save only preferences/checkpoint; preserve concurrent learning state."""
        with self.transaction() as db:
            row = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .with_for_update()).mappings().one()
            current = LearnerProfile.model_validate(row['data'])
            if current.onboarding.revision != expected_revision:
                raise ValueError('Onboarding changed. Reload your saved progress before continuing.')
            if current.onboarding_status == 'complete':
                raise ValueError('Onboarding is already complete.')
            for field in ('name', 'target_language', 'goal', 'interests', 'target_situations', 'onboarding_status', 'onboarding', 'accent_preferences'):
                setattr(current, field, getattr(profile, field))
            current.onboarding.revision = expected_revision + 1
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .values(**profile_values(current)))
        return current

    def current_exercise(self):
        with self.read() as db:
            row = db.execute(select(s.exercises).where(s.exercises.c.user_id == self.learner_id)
                .order_by(s.exercises.c.created_at.desc(), s.exercises.c.id.desc()).limit(1)).mappings().first()
            return api_row(row)

    def exercise(self, exercise_id):
        with self.read() as db:
            return api_row(db.execute(select(s.exercises).where(and_(s.exercises.c.id == exercise_id,
                s.exercises.c.user_id == self.learner_id))).mappings().first())

    def owned_exercise(self, db, exercise_id):
        row = db.execute(select(s.exercises).where(and_(s.exercises.c.id == exercise_id,
            s.exercises.c.user_id == self.learner_id))).mappings().first()
        if row is None:
            raise ValueError('Exercise not found for this learner')
        return row

    def owned_attempt(self, db, attempt_id):
        row = db.execute(select(s.attempts).join(s.exercises).where(and_(s.attempts.c.id == attempt_id,
            s.exercises.c.user_id == self.learner_id))).mappings().first()
        if row is None:
            raise ValueError('Attempt not found for this learner')
        return row

    def save_exercise(self, exercise_id, content, difficulty, audio_name, audio=None, lesson=None, onboarding_revision=None):
        session_id = str(uuid4())
        with self.transaction() as db:
            self.check_audio_owner(db, audio_name)
            profile = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id).with_for_update()).mappings().one()
            if onboarding_revision is not None and profile['data']['onboarding']['revision'] != onboarding_revision:
                raise ValueError('Onboarding changed; discard generated exercise')
            open_lesson = db.execute(select(s.lessons).where(and_(s.lessons.c.user_id == self.learner_id,
                s.lessons.c.status.in_(s.OPEN_LESSON_STATUSES))).with_for_update()).mappings().first()
            if open_lesson:
                from app.lesson import Lesson, closing_due
                checkpoint = Lesson.model_validate(open_lesson['data'])
                if (lesson is None or checkpoint.id != lesson.id or checkpoint.revision != lesson.revision
                        or checkpoint.status != 'active' or checkpoint.phase != 'EXERCISE'
                        or checkpoint.exercise_id or closing_due(checkpoint, utcnow(), self.wall_clock)):
                    raise ValueError('Lesson changed or closing is due; reload the lesson.')
            from app.accent_practice import authorized_accent
            if content.passage_voice and content.passage_voice.accent != 'default':
                if open_lesson is None or content.passage_voice.accent != authorized_accent(checkpoint):
                    raise ValueError('Passage accent consent is missing or expired; retry ordinary practice')
            if profile['data']['difficulty'] != difficulty.model_dump():
                raise ValueError('Learner difficulty changed; retry exercise generation')
            current = db.execute(select(s.exercises.c.id).where(s.exercises.c.user_id == self.learner_id)
                .order_by(s.exercises.c.created_at.desc(), s.exercises.c.id.desc()).limit(1)).scalar_one_or_none()
            if current and db.execute(select(s.attempts.c.id).where(and_(s.attempts.c.exercise_id == current,
                    s.attempts.c.status == 'evaluated'))).first() is None:
                raise ValueError('Resume the current unfinished exercise')
            # One exercise per session preserves today's flow. Future session orchestration
            # can group several exercises without changing exercise/attempt ownership.
            db.execute(s.sessions.insert().values(id=session_id, user_id=self.learner_id,
                status='active', started_at=utcnow(), starting_difficulty=difficulty.model_dump()))
            db.execute(s.exercises.insert().values(id=exercise_id, user_id=self.learner_id,
                session_id=session_id, content=content.model_dump(mode='json'), difficulty=difficulty.model_dump(),
                audio_name=audio_name, created_at=utcnow()))
            if audio is not None:
                self.save_audio(db, audio_name, audio)
            if lesson is not None:
                if open_lesson is None:
                    raise ValueError('No active lesson')
                db.execute(s.lesson_exercises.insert().values(exercise_id=exercise_id,
                    lesson_id=lesson.id, user_id=self.learner_id))
                updated = lesson.model_copy(deep=True)
                updated.exercise_id = exercise_id
                if updated.accent_consent.state == 'accepted':
                    updated.accent_consent.state = 'consumed' if authorized_accent(checkpoint) else 'expired'
                updated.passage_accent = None
                updated.passage_fallback = bool(content.passage_voice and content.passage_voice.fallback)
                updated.revision += 1
                db.execute(update(s.lessons).where(s.lessons.c.id == lesson.id).values(
                    revision=updated.revision, data=updated.model_dump(mode='json')))

    def add_attempt(self, exercise_id, transcription: Transcription):
        attempt_id = str(uuid4())
        with self.transaction() as db:
            self.owned_exercise(db, exercise_id)
            db.execute(s.attempts.insert().values(id=attempt_id, exercise_id=exercise_id,
                transcription=transcription.model_dump(mode='json'), recognized_text=transcription.text,
                confidence=transcription.confidence, transcription_source=transcription.source,
                status='transcribed', created_at=utcnow()))
        return attempt_id

    def attempt(self, attempt_id):
        with self.read() as db:
            row = db.execute(select(s.attempts).join(s.exercises).where(and_(s.attempts.c.id == attempt_id,
                s.exercises.c.user_id == self.learner_id))).mappings().first()
            return api_row(row)

    def completed(self, exercise_id):
        with self.read() as db:
            return db.execute(select(s.attempts.c.id).join(s.exercises).where(and_(
                s.attempts.c.exercise_id == exercise_id, s.attempts.c.status == 'evaluated',
                s.exercises.c.user_id == self.learner_id))).scalar_one_or_none()

    def result(self, attempt_id):
        with self.read() as db:
            row = db.execute(select(s.evaluations.c.data.label('evaluation'), s.events.c.data.label('adaptation'))
                .select_from(s.attempts.join(s.exercises).join(s.evaluations).join(s.events))
                .where(and_(s.attempts.c.id == attempt_id, s.exercises.c.user_id == self.learner_id))).mappings().first()
            return dict(row) if row else None

    def complete(self, attempt_id, text, evaluation, event, profile, conversation=None):
        with self.transaction() as db:
            # Serialize completions for this learner across processes as well as tasks.
            db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == self.learner_id).with_for_update()).one()
            attempt = self.owned_attempt(db, attempt_id)
            exercise = self.owned_exercise(db, attempt['exercise_id'])
            duplicate = db.execute(select(s.attempts.c.id).where(and_(
                s.attempts.c.exercise_id == exercise['id'], s.attempts.c.status == 'evaluated'))).first()
            if duplicate:
                raise ValueError('Exercise already evaluated')
            # Do not overwrite a newer difficulty with adaptation computed from stale state.
            current = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id)).mappings().one()
            if current['completed_attempts'] != profile.completed_attempts - 1 or current['data']['difficulty'] != event.old_difficulty.model_dump():
                raise ValueError('Learner state changed; retry assessment')
            if conversation is not None and conversation.exercise_id != exercise['id']:
                raise ValueError('Conversation does not belong to this attempt')
            updated = profile.model_copy(deep=True)
            # A preference changed while evidence was evaluated must survive completion.
            updated.tutor_voice = current['data'].get('tutor_voice', 'original')
            if updated.initial_listening_profile is None and not getattr(evaluation, 'insufficient_evidence', []):
                updated.initial_listening_profile = updated.listening_profile.model_copy(deep=True)
            db.execute(update(s.attempts).where(s.attempts.c.id == attempt_id).values(status='evaluated', confirmed_text=text))
            db.execute(s.evaluations.insert().values(attempt_id=attempt_id, **evaluation_values(evaluation)))
            db.execute(s.events.insert().values(id=str(uuid4()), attempt_id=attempt_id, **event_values(event)))
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id).values(**profile_values(updated)))
            if conversation is not None:
                self.write_conversation(db, conversation)
            db.execute(update(s.sessions).where(s.sessions.c.id == exercise['session_id']).values(status='completed', completed_at=utcnow()))

    def history(self):
        with self.read() as db:
            rows = db.execute(select(s.attempts.c.id, s.attempts.c.exercise_id, s.attempts.c.created_at,
                s.evaluations.c.data.label('evaluation'), s.events.c.data.label('adaptation'))
                .select_from(s.attempts.join(s.exercises).join(s.evaluations).join(s.events))
                .where(s.exercises.c.user_id == self.learner_id)
                .order_by(s.attempts.c.created_at, s.attempts.c.id)).mappings().all()
            return [api_row(row) for row in rows]

    def session_history(self, completed_since: datetime | None = None, before: datetime | None = None):
        """UTC completion-range query; caller must supply any future learner-day policy."""
        query = select(s.sessions).where(s.sessions.c.user_id == self.learner_id)
        for value in (completed_since, before):
            if value is not None and value.tzinfo is None:
                raise ValueError('Session query timestamps must include a timezone')
        if completed_since is not None:
            query = query.where(s.sessions.c.completed_at >= completed_since)
        if before is not None:
            query = query.where(s.sessions.c.completed_at < before)
        with self.read() as db:
            rows = db.execute(query.order_by(s.sessions.c.started_at.desc(), s.sessions.c.id.desc())).mappings().all()
            return [TrainingSession(id=row['id'], learner_id=row['user_id'], status=row['status'],
                started_at=aware(row['started_at']), completed_at=aware(row['completed_at']) if row['completed_at'] else None,
                starting_difficulty=row['starting_difficulty']) for row in rows]

    def session_exercises(self, session_id):
        with self.read() as db:
            rows = db.execute(select(s.exercises).join(s.sessions, s.exercises.c.session_id == s.sessions.c.id).where(and_(s.sessions.c.id == session_id,
                s.sessions.c.user_id == self.learner_id, s.exercises.c.user_id == self.learner_id))
                .order_by(s.exercises.c.created_at)).mappings().all()
            return [api_row(row) for row in rows]

    def conversation(self, exercise_id):
        with self.read() as db:
            data = db.execute(select(s.conversations.c.data).join(s.exercises).where(and_(
                s.conversations.c.exercise_id == exercise_id, s.exercises.c.user_id == self.learner_id))).scalar_one_or_none()
            return Conversation.model_validate(data) if data else None

    def write_conversation(self, db, conversation: Conversation):
        self.owned_exercise(db, conversation.exercise_id)
        if conversation.pending_attempt_id:
            if self.owned_attempt(db, conversation.pending_attempt_id)['exercise_id'] != conversation.exercise_id:
                raise ValueError('Pending attempt belongs to another exercise')
        now = utcnow()
        self.upsert(db, s.conversations, dict(exercise_id=conversation.exercise_id,
            data=conversation.model_dump(mode='json'), state=conversation.state,
            pending_attempt_id=conversation.pending_attempt_id,
            active_followup_id=conversation.active_followup.id if conversation.active_followup else None, updated_at=now), ['exercise_id'])
        # Stable IDs preserve issued-question and accepted-turn timestamps on retries.
        for position, question in enumerate(conversation.followups):
            values = dict(id=question.id, exercise_id=conversation.exercise_id, position=position,
                dimension=question.dimension, unit_index=question.index, question=question.question, created_at=now)
            prior = db.execute(select(s.followups).where(s.followups.c.id == question.id)).mappings().first()
            if prior is not None:
                if prior['exercise_id'] != conversation.exercise_id:
                    raise ValueError('Follow-up belongs to another exercise')
                values['created_at'] = prior['created_at']
            self.upsert(db, s.followups, values, ['id'])
        for position, turn in enumerate(conversation.turns):
            if self.owned_attempt(db, turn.attempt_id)['exercise_id'] != conversation.exercise_id:
                raise ValueError('Turn belongs to another exercise')
            if turn.followup and turn.followup.id not in {question.id for question in conversation.followups}:
                raise ValueError('Turn has an unissued follow-up')
            prior = db.execute(select(s.turns.c.created_at).where(s.turns.c.attempt_id == turn.attempt_id)).scalar_one_or_none()
            self.upsert(db, s.turns, dict(attempt_id=turn.attempt_id, exercise_id=conversation.exercise_id,
                followup_id=turn.followup.id if turn.followup else None, position=position,
                confirmed_text=turn.text, created_at=prior or now), ['attempt_id'])
        if conversation.assessment and conversation.turns:
            attempt_id = conversation.turns[-1].attempt_id
            insert = postgres_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
            db.execute(insert(s.assessments).values(attempt_id=attempt_id, exercise_id=conversation.exercise_id,
                data=conversation.assessment.model_dump(mode='json'), created_at=now).on_conflict_do_nothing())
            for unit in conversation.assessment.units:
                db.execute(insert(s.evidence).values(attempt_id=attempt_id, exercise_id=conversation.exercise_id,
                    dimension=unit.dimension, unit_index=unit.index, status=unit.status,
                    evidence=unit.evidence, question=unit.question).on_conflict_do_nothing())

    def save_conversation(self, conversation):
        with self.transaction() as db:
            self.write_conversation(db, conversation)

    def save_conversation_state(self, conversation):
        """AUD-84 P5: persist a state-only change (for example READY_FOR_NEXT) with one UPDATE of the conversation row.

        ``write_conversation`` rewrites the whole conversation (every follow-up, turn, assessment and evidence row) on every
        save. When only the conversation's own state changed, those child rows are already stored exactly as they would be
        rewritten (they are written together with the row, from the same object), so only the ``conversations`` row needs
        updating, with exactly the values ``write_conversation`` would give it. The UPDATE is scoped to this learner's
        exercise in the same statement. Anything unusual falls back to the unchanged full save: a pending attempt (its
        ownership check), a missing row (the full save re-inserts it) or an exercise that is not this learner's (the full
        save raises the same error)."""
        if conversation.pending_attempt_id is None:
            with self.transaction() as db:
                owned = exists().where(and_(s.exercises.c.id == conversation.exercise_id, s.exercises.c.user_id == self.learner_id))
                updated = db.execute(update(s.conversations).where(and_(s.conversations.c.exercise_id == conversation.exercise_id, owned)).values(
                    data=conversation.model_dump(mode='json'), state=conversation.state, pending_attempt_id=None,
                    active_followup_id=conversation.active_followup.id if conversation.active_followup else None, updated_at=utcnow()))
                if updated.rowcount == 1:
                    return
        self.save_conversation(conversation)

    def confirm_text(self, attempt_id, text):
        with self.transaction() as db:
            self.owned_attempt(db, attempt_id)
            db.execute(update(s.attempts).where(s.attempts.c.id == attempt_id).values(confirmed_text=text))

    def prepare_assessment(self, attempt_id: str, text: str, conversation: Conversation):
        """Durably save recognition confirmation and retry checkpoint together."""
        with self.transaction() as db:
            attempt = self.owned_attempt(db, attempt_id)
            if (attempt['exercise_id'] != conversation.exercise_id
                    or conversation.pending_attempt_id != attempt_id
                    or conversation.pending_text != text):
                raise ValueError('Assessment checkpoint does not match the confirmed attempt')
            db.execute(update(s.attempts).where(s.attempts.c.id == attempt_id).values(confirmed_text=text))
            self.write_conversation(db, conversation)

    def coach_audio(self, exercise_id, cue_id):
        with self.read() as db:
            return db.execute(select(s.coach_audio.c.audio_name).join(s.exercises).where(and_(
                s.coach_audio.c.exercise_id == exercise_id, s.coach_audio.c.cue_id == cue_id,
                s.exercises.c.user_id == self.learner_id))).scalar_one_or_none()

    def save_audio(self, db, audio_name, data):
        if not data:
            raise ValueError('Empty generated audio')
        self.check_audio_owner(db, audio_name)
        self.upsert(db, s.audio_assets, dict(audio_name=audio_name, user_id=self.learner_id, data=data, created_at=utcnow()), ['audio_name'])

    def check_audio_owner(self, db, audio_name):
        prior = db.execute(select(s.audio_assets.c.user_id).where(s.audio_assets.c.audio_name == audio_name)).scalar_one_or_none()
        if prior is not None and prior != self.learner_id:
            raise ValueError('Audio belongs to another learner')

    def audio_blob(self, audio_name):
        with self.read() as db:
            return db.execute(select(s.audio_assets.c.data).where(and_(s.audio_assets.c.audio_name == audio_name,
                s.audio_assets.c.user_id == self.learner_id))).scalar_one_or_none()

    def owns_audio(self, audio_name):
        with self.read() as db:
            return db.execute(select(s.audio_assets.c.audio_name).where(and_(
                s.audio_assets.c.audio_name == audio_name, s.audio_assets.c.user_id == self.learner_id))).first() is not None

    def save_coach_audio(self, exercise_id, cue_id, audio_name, audio=None):
        with self.transaction() as db:
            self.owned_exercise(db, exercise_id)
            self.check_audio_owner(db, audio_name)
            self.upsert(db, s.coach_audio, dict(exercise_id=exercise_id, cue_id=cue_id, audio_name=audio_name), ['exercise_id', 'cue_id'])
            if audio is not None:
                self.save_audio(db, audio_name, audio)

    def optional_roadmap(self):
        """Recoverable best-effort refresh, outside authoritative lesson transactions."""
        try:
            roadmap = self.roadmap()
            if roadmap is None or len(roadmap.upcoming) != 3:
                raise ValueError('Planner outlines unavailable')
            return roadmap
        except Exception as exc:
            # No exception messages, SQL parameters, owner IDs or learner text.
            logging.getLogger('audli').warning('Planner refresh deferred; exception=%s', type(exc).__name__)
            return None

    def roadmap(self, **kwargs):
        from app.storage.planner import access
        return access(self, **kwargs)

    def lesson(self, lesson_id=None, completed=False):
        from app.lesson import Lesson
        query = select(s.lessons).where(s.lessons.c.user_id == self.learner_id)
        if lesson_id:
            query = query.where(s.lessons.c.id == lesson_id)
        else:
            query = query.where(s.lessons.c.status == 'completed' if completed else s.lessons.c.status.in_(s.OPEN_LESSON_STATUSES))
        with self.read() as db:
            row = db.execute(query.order_by(s.lessons.c.started_at.desc(), s.lessons.c.id.desc()).limit(1)).mappings().first()
            return Lesson.model_validate(row['data']) if row else None

    def accent_lesson_history(self):
        from app.lesson import Lesson
        with self.read() as db:
            rows = db.execute(select(s.lessons.c.data).where(s.lessons.c.user_id == self.learner_id)
                .order_by(s.lessons.c.started_at.desc(), s.lessons.c.id.desc())).scalars().all()
            return [Lesson.model_validate(row) for row in rows]

    def require_lesson_limit_schema(self):
        """Fail closed at startup when the limit is switched on before migration 006 is applied."""
        from sqlalchemy import inspect
        with self.engine.connect() as db:
            inspector = inspect(db)
            ready = inspector.has_table('lesson_entitlements') and set(s.lesson_entitlements.columns.keys()).issubset(
                {column['name'] for column in inspector.get_columns('lesson_entitlements')})
            if ready:
                ready = db.execute(select(s.versions.c.version).where(s.versions.c.version == 6)).first() is not None
        if not ready:
            raise RuntimeError('AUDLI_LESSON_LIMIT_MODE requires migration 006 (lesson_entitlements); '
                               'apply it as the database owner or set the mode to off.')

    def require_review_improvement_schema(self):
        """Fail closed at startup when the feature is switched on before migration 007 is applied."""
        from sqlalchemy import inspect
        with self.engine.connect() as db:
            inspector = inspect(db)
            ready = inspector.has_table('review_improvements') and set(s.review_improvements.columns.keys()).issubset(
                {column['name'] for column in inspector.get_columns('review_improvements')})
            if ready:
                ready = db.execute(select(s.versions.c.version).where(s.versions.c.version == 7)).first() is not None
        if not ready:
            raise RuntimeError('AUDLI_REVIEW_IMPROVEMENT_ENABLED requires migration 007 (review_improvements); '
                               'apply it as the database owner or leave the feature off.')

    def _entitlement(self, db):
        """Effective 24-hour window. The ledger is authoritative; a lesson row started later than the ledger
        (written by a release that predates it) is honored until the next claim re-syncs the ledger."""
        ledger = db.execute(select(s.lesson_entitlements).where(
            s.lesson_entitlements.c.user_id == self.learner_id)).mappings().first()
        latest = db.execute(select(s.lessons.c.id, s.lessons.c.started_at).where(s.lessons.c.user_id == self.learner_id)
            .order_by(s.lessons.c.started_at.desc(), s.lessons.c.id.desc()).limit(1)).mappings().first()
        if latest is not None and (ledger is None or aware(latest['started_at']) > aware(ledger['last_started_at'])):
            started = aware(latest['started_at'])
            return dict(last_started_at=started, next_eligible_at=started + WINDOW,
                        last_lesson_id=latest['id'], voided_at=None, in_ledger=ledger is not None)
        if ledger is None:
            return None
        return dict(last_started_at=aware(ledger['last_started_at']), next_eligible_at=aware(ledger['next_eligible_at']),
                    last_lesson_id=ledger['last_lesson_id'], voided_at=ledger['voided_at'], in_ledger=True)

    def claim_lesson_start(self, db, lesson, mode):
        """Record the start in the learner's durable ledger inside the lesson-creation transaction.

        off: untouched. observe: the ledger tracks the as-if-enforced window and a start inside it is only
        logged. enforce (not yet selectable by configuration) refuses it with LessonCooldown."""
        if mode == 'off':
            return None
        at = aware(lesson.started_at)
        current = self._entitlement(db)
        if current is not None and at < current['next_eligible_at']:
            if mode == 'enforce':
                raise LessonCooldown(current['next_eligible_at'])
            LOG.info('lesson_limit would_refuse code=lesson_cooldown where=lesson.start learner=%s next_eligible_at=%s',
                     learner_tag(self.learner_id), current['next_eligible_at'].isoformat())
            return 'would_refuse'
        values = dict(user_id=self.learner_id, last_started_at=at, next_eligible_at=at + WINDOW,
                      last_lesson_id=lesson.id, voided_at=None, first_speech_failures=0, grace_actions=0, updated_at=utcnow())
        insert = postgres_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        statement = insert(s.lesson_entitlements).values(**values)
        # One atomic statement: the window may only advance when it has already elapsed.
        result = db.execute(statement.on_conflict_do_update(index_elements=['user_id'],
            set_={key: getattr(statement.excluded, key) for key in values if key != 'user_id'},
            where=s.lesson_entitlements.c.next_eligible_at <= at).returning(s.lesson_entitlements.c.user_id))
        if result.first() is None:
            if mode == 'enforce':
                raise LessonCooldown(self._entitlement(db)['next_eligible_at'])
            return 'would_refuse'
        return 'claimed'

    def take_grace_slot(self, lesson_id, limit=None, at=None):
        """Take one provider-backed wrap-up slot for THIS lesson after its deadline. Returns the new count, or None
        when refused.

        One atomic statement shared by every worker and surviving restarts. It matches the ledger's current
        lesson only, so a delayed request from an older lesson can never consume the next lesson's allowance.
        A slot is consumed on admission and never refunded: a failed or cancelled provider call still counts,
        so retries cannot be unlimited. Cached replays never reach this method."""
        limit = GRACE_ACTION_LIMIT if limit is None else limit
        at = at or utcnow()
        with self.transaction() as db:
            row = db.execute(update(s.lesson_entitlements).where(and_(
                s.lesson_entitlements.c.user_id == self.learner_id, s.lesson_entitlements.c.last_lesson_id == lesson_id,
                s.lesson_entitlements.c.voided_at.is_(None), s.lesson_entitlements.c.grace_actions < limit))
                .values(grace_actions=s.lesson_entitlements.c.grace_actions + 1, updated_at=at)
                .returning(s.lesson_entitlements.c.grace_actions)).first()
        return row[0] if row else None

    def eligibility(self, mode, at=None):
        from app.lesson_limit import OpenLesson, LastLesson, evaluate, LESSON_MAX_SECONDS
        at = at or utcnow()
        if mode == 'off':
            return evaluate(mode, at, None, None, None)
        with self.read() as db:
            current = self._entitlement(db)
            row = db.execute(select(s.lessons.c.id, s.lessons.c.status, s.lessons.c.started_at).where(and_(
                s.lessons.c.user_id == self.learner_id, s.lessons.c.status.in_(s.OPEN_LESSON_STATUSES)))).mappings().first()
            newest = db.execute(select(s.lessons.c.id, s.lessons.c.status).where(s.lessons.c.user_id == self.learner_id)
                .order_by(s.lessons.c.started_at.desc(), s.lessons.c.id.desc()).limit(1)).mappings().first()
        opened = None
        if row:
            started = aware(row['started_at'])
            opened = OpenLesson(id=row['id'], status=row['status'], started_at=started,
                                expires_at=started + timedelta(seconds=LESSON_MAX_SECONDS),
                                grace_ends_at=started + timedelta(seconds=LESSON_MAX_SECONDS + GRACE_SECONDS))
            if mode == 'enforce' and at >= opened.grace_ends_at:
                opened = None  # Server-side expiry is pending; the learner is in cooldown, not mid-lesson.
                newest = {'id': row['id'], 'status': 'expired'}
        last = LastLesson(id=newest['id'], status=newest['status']) if newest else None
        return evaluate(mode, at, current and current['next_eligible_at'], current and current['last_started_at'], opened, last)

    def cooldown_until(self, at=None):
        """The instant the next lesson opens, or None when the learner may start one."""
        at = at or utcnow()
        with self.read() as db:
            current = self._entitlement(db)
        return current['next_eligible_at'] if current is not None and at < current['next_eligible_at'] else None

    def latest_lesson(self):
        from app.lesson import Lesson
        with self.read() as db:
            row = db.execute(select(s.lessons.c.data).where(s.lessons.c.user_id == self.learner_id)
                .order_by(s.lessons.c.started_at.desc(), s.lessons.c.id.desc()).limit(1)).scalar_one_or_none()
        return Lesson.model_validate(row) if row else None

    def _expire(self, db, lesson_id, at):
        """Mark an open lesson expired inside the caller's transaction. Work stays; it is never completed."""
        from app.lesson import Lesson, elapsed
        row = db.execute(select(s.lessons.c.data, s.lessons.c.revision, s.lessons.c.status).where(and_(
            s.lessons.c.id == lesson_id, s.lessons.c.user_id == self.learner_id)).with_for_update()).mappings().first()
        if row is None or row['status'] not in s.OPEN_LESSON_STATUSES:
            return None
        lesson = Lesson.model_validate(row['data'])
        lesson.elapsed_seconds = elapsed(lesson, at, True)
        lesson.active_since = None
        lesson.status = 'expired'
        lesson.revision = row['revision'] + 1
        db.execute(update(s.lessons).where(s.lessons.c.id == lesson_id).values(
            status='expired', revision=lesson.revision, data=lesson.model_dump(mode='json')))
        return lesson

    def expire_lesson(self, lesson_id, at=None):
        """Server-side expiry; a no-op unless the lesson is still open. Returns the expired lesson or None."""
        at = at or utcnow()
        with self.transaction() as db:
            db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == self.learner_id).with_for_update()).one()
            return self._expire(db, lesson_id, at)

    FIRST_SPEECH_FAILURE_LIMIT = 2

    def note_first_speech(self, lesson_id, ok, at=None):
        """Track the provider outcome of a lesson's first tutor speech (revision 0 only).

        Returns 'ignored', 'ok', 'counted' or 'voided'. After two consecutive verified provider failures the
        lesson is expired and its allowance returned in one transaction. Only the ledger's current, unvoided,
        untouched lesson qualifies, so abandonment, microphone denial and client retries never void."""
        at = at or utcnow()
        with self.transaction() as db:
            db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == self.learner_id).with_for_update()).one()
            ledger = db.execute(select(s.lesson_entitlements).where(
                s.lesson_entitlements.c.user_id == self.learner_id)).mappings().first()
            lesson = db.execute(select(s.lessons.c.revision, s.lessons.c.status).where(and_(
                s.lessons.c.id == lesson_id, s.lessons.c.user_id == self.learner_id))).mappings().first()
            if (ledger is None or ledger['last_lesson_id'] != lesson_id or ledger['voided_at'] is not None
                    or lesson is None or lesson['revision'] != 0 or lesson['status'] != 'active'):
                return 'ignored'
            failures = 0 if ok else ledger['first_speech_failures'] + 1
            if ok and not ledger['first_speech_failures']:
                return 'ok'
            values = dict(first_speech_failures=failures, updated_at=at)
            if failures >= self.FIRST_SPEECH_FAILURE_LIMIT:
                values.update(next_eligible_at=ledger['last_started_at'], voided_at=at)
                self._expire(db, lesson_id, at)
            db.execute(update(s.lesson_entitlements).where(s.lesson_entitlements.c.user_id == self.learner_id).values(**values))
            return 'ok' if ok else 'voided' if failures >= self.FIRST_SPEECH_FAILURE_LIMIT else 'counted'

    def void_allowance(self, lesson_id, at=None):
        """Give back the allowance for a lesson that never reached its first tutor interaction.

        Only the ledger's current, unvoided lesson at revision 0 qualifies. The caller must have verified a
        server/provider failure; ordinary abandonment, microphone denial and client retries never call this."""
        at = at or utcnow()
        with self.transaction() as db:
            revision = db.execute(select(s.lessons.c.revision).where(and_(s.lessons.c.id == lesson_id,
                s.lessons.c.user_id == self.learner_id))).scalar_one_or_none()
            if revision != 0:
                return False
            changed = db.execute(update(s.lesson_entitlements).where(and_(
                s.lesson_entitlements.c.user_id == self.learner_id, s.lesson_entitlements.c.last_lesson_id == lesson_id,
                s.lesson_entitlements.c.voided_at.is_(None),
                s.lesson_entitlements.c.next_eligible_at > s.lesson_entitlements.c.last_started_at))
                .values(next_eligible_at=s.lesson_entitlements.c.last_started_at, voided_at=at, updated_at=at))
            return changed.rowcount == 1

    def create_lesson(self, lesson, remember_only=True, limit_mode=None):
        limit_mode = self.limit_mode if limit_mode is None else limit_mode
        with self.transaction() as db:
            row = db.execute(select(s.profiles.c.data).where(s.profiles.c.user_id == self.learner_id).with_for_update()).scalar_one()
            if db.execute(select(s.lessons.c.id).where(and_(s.lessons.c.user_id == self.learner_id,
                    s.lessons.c.status.in_(s.OPEN_LESSON_STATUSES)))).first():
                raise ValueError('Resume the existing lesson.')
            completed = db.execute(select(s.lessons.c.data).where(and_(s.lessons.c.user_id == self.learner_id,
                s.lessons.c.status == 'completed')).order_by(s.lessons.c.started_at.desc(), s.lessons.c.id.desc())).scalars().all()
            preferences = LearnerProfile.model_validate(row).accent_preferences
            # Every other returning lesson; rotate recognized labels and never
            # repeat the last suggestion. A single preference is suggested once.
            last = next((item.get('suggested_accent') for item in completed if item.get('suggested_accent')), None)
            if remember_only and lesson.previous and len(completed) % 2 == 1 and preferences.status == 'preferred':
                lesson.suggested_accent = next((item for item in preferences.accents if item != last), None)
            # Same transaction as the lesson row: a failed start never consumes the allowance.
            self.claim_lesson_start(db, lesson, limit_mode)
            db.execute(s.lessons.insert().values(id=lesson.id, user_id=self.learner_id,
                status=lesson.status, revision=lesson.revision, started_at=lesson.started_at,
                data=lesson.model_dump(mode='json')))

    def save_lesson(self, lesson, expected_revision, interests=None, accent_preferences=None):
        """CAS checkpoint plus preferences in one transaction; never replace learning state."""
        with self.transaction() as db:
            profile = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id).with_for_update()).mappings().one()
            lesson.revision = expected_revision + 1
            changed = db.execute(update(s.lessons).where(and_(s.lessons.c.id == lesson.id,
                s.lessons.c.user_id == self.learner_id, s.lessons.c.revision == expected_revision))
                .values(status=lesson.status, revision=lesson.revision, data=lesson.model_dump(mode='json')))
            if changed.rowcount != 1:
                raise ValueError('Lesson changed. Reload the current checkpoint.')
            if interests is not None or accent_preferences is not None:
                current = LearnerProfile.model_validate(profile['data'])
                if interests is not None:
                    current.interests = interests
                if accent_preferences is not None:
                    current.accent_preferences = accent_preferences
                db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id).values(**profile_values(current)))
        return lesson

    def lesson_work(self, lesson_id):
        """Only completed owned evidence; pending scripts never cross the lesson API."""
        with self.read() as db:
            rows = db.execute(select(s.exercises.c.id.label('exercise_id'), s.exercises.c.content, s.evaluations.c.data.label('evaluation'))
                .select_from(s.lesson_exercises.join(s.exercises).join(s.attempts).join(s.evaluations))
                .where(and_(s.lesson_exercises.c.lesson_id == lesson_id,
                    s.lesson_exercises.c.user_id == self.learner_id, s.exercises.c.user_id == self.learner_id,
                    s.attempts.c.status == 'evaluated'))
                .order_by(s.exercises.c.created_at)).mappings().all()
            return [dict(row) for row in rows]

    def lesson_for_exercise(self, exercise_id):
        from app.lesson import Lesson
        with self.read() as db:
            data = db.execute(select(s.lessons.c.data).join(s.lesson_exercises)
                .where(and_(s.lesson_exercises.c.exercise_id == exercise_id,
                    s.lesson_exercises.c.user_id == self.learner_id,
                    s.lessons.c.user_id == self.learner_id))).scalar_one_or_none()
            return Lesson.model_validate(data) if data else None

    def attach_lesson_exercise(self, lesson, exercise_id):
        from app.lesson import closing_due
        with self.transaction() as db:
            db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == self.learner_id).with_for_update()).one()
            exercise = self.owned_exercise(db, exercise_id)
            if closing_due(lesson, utcnow(), self.wall_clock):
                raise ValueError('Closing is due.')
            lesson.exercise_id = exercise_id
            from app.accent_practice import authorized_accent
            if authorized_accent(lesson):
                # This audio predates the grant. Keep its replay/progress and
                # reserve the grant for the next newly generated passage.
                lesson.accent_consent.exercise_number = lesson.exercises_completed + 1
            voice = exercise['content'].get('passage_voice')
            lesson.passage_fallback = bool(voice and voice.get('fallback'))
            lesson.revision += 1
            result = db.execute(update(s.lessons).where(and_(s.lessons.c.id == lesson.id,
                s.lessons.c.user_id == self.learner_id, s.lessons.c.revision == lesson.revision - 1,
                s.lessons.c.status == 'active')).values(revision=lesson.revision, data=lesson.model_dump(mode='json')))
            if result.rowcount != 1:
                raise ValueError('Lesson changed.')
            db.execute(s.lesson_exercises.insert().values(lesson_id=lesson.id,
                exercise_id=exercise_id, user_id=self.learner_id))

    def previous_lesson(self):
        from app.lesson import previous_copy
        previous = self.lesson(completed=True)
        if previous:
            work = self.lesson_work(previous.id)
            return previous_copy(work[-1]['content']['topic'], [item['evaluation'] for item in work]) if work else None
        # Migration bridge: old training sessions are real, completed single-exercise sessions.
        sessions = [item for item in self.session_history() if item.status == 'completed']
        if not sessions:
            return None
        rows = self.session_exercises(sessions[0].id)
        completed = [row for row in rows if self.completed(row['id'])]
        if not completed:
            return None
        return previous_copy(json.loads(completed[-1]['content'])['topic'],
            [self.result(self.completed(row['id']))['evaluation'] for row in completed])

    def cache_lesson_audio(self, name, data):
        with self.transaction() as db:
            self.save_audio(db, name, data)
