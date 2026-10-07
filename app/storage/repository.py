"""Storage-independent operations shared by the SQLite and Postgres adapters."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from app.models import LearnerProfile, Conversation, TrainingSession, Transcription
from app.storage import schema as s


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


class SQLProgressRepository:
    """Application repository contract; adapters supply an engine and learner scope.

    Domain snapshots keep existing API formats. Relational columns/projections are
    written in the same transaction and support history, ownership and future policy.
    """
    def __init__(self, engine, learner_id: str | None):
        self.engine = engine
        self.learner_id = learner_id

    def for_learner(self, learner_id: str):
        """A new request scope; never mutate the pool owner's identity or dispose it."""
        scoped = SQLProgressRepository(self.engine, learner_id)
        scoped.initialize_learner()
        return scoped

    def close(self):
        self.engine.dispose()

    @contextmanager
    def transaction(self):
        with self.engine.connect() as db:
            # SQLite needs the write lock before reading; Postgres uses row locks.
            if self.engine.dialect.name == 'sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                db.begin()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def upsert(self, db, table, values, keys):
        insert = postgres_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        statement = insert(table).values(**values)
        db.execute(statement.on_conflict_do_update(index_elements=keys,
            set_={key: getattr(statement.excluded, key) for key in values if key not in keys}))

    def initialize_learner(self):
        insert = postgres_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        with self.transaction() as db:
            db.execute(insert(s.users).values(id=self.learner_id, created_at=utcnow()).on_conflict_do_nothing())
            db.execute(insert(s.profiles).values(user_id=self.learner_id,
                **profile_values(LearnerProfile())).on_conflict_do_nothing())

    def profile(self):
        with self.engine.connect() as db:
            row = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id)).mappings().one()
            return LearnerProfile(onboarding=row['data'].get('onboarding', {}), name=row['name'], target_language=row['target_language'], goal=row['goal'],
                interests=row['interests'], target_situations=row['target_situations'],
                onboarding_status=row['onboarding_status'], initial_listening_profile=row['initial_listening_profile'],
                listening_profile={key: row[key] for key in ('overall', 'main_idea', 'details', 'vocabulary', 'inference', 'natural_speed')},
                difficulty={key: row[key] for key in ('speech_rate', 'duration_seconds', 'vocabulary_level', 'information_density',
                    'speaker_count', 'accent_complexity', 'background_noise')}, focus=row['focus'], completed_attempts=row['completed_attempts'])

    def save_profile(self, profile: LearnerProfile):
        with self.transaction() as db:
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id).values(**profile_values(profile)))

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
            for field in ('name', 'target_language', 'goal', 'interests', 'target_situations', 'onboarding_status', 'onboarding'):
                setattr(current, field, getattr(profile, field))
            current.onboarding.revision = expected_revision + 1
            db.execute(update(s.profiles).where(s.profiles.c.user_id == self.learner_id)
                .values(**profile_values(current)))
        return current

    def current_exercise(self):
        with self.engine.connect() as db:
            row = db.execute(select(s.exercises).where(s.exercises.c.user_id == self.learner_id)
                .order_by(s.exercises.c.created_at.desc(), s.exercises.c.id.desc()).limit(1)).mappings().first()
            return api_row(row)

    def exercise(self, exercise_id):
        with self.engine.connect() as db:
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

    def save_exercise(self, exercise_id, content, difficulty, audio_name, audio=None):
        session_id = str(uuid4())
        with self.transaction() as db:
            self.check_audio_owner(db, audio_name)
            profile = db.execute(select(s.profiles).where(s.profiles.c.user_id == self.learner_id).with_for_update()).mappings().one()
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
        with self.engine.connect() as db:
            row = db.execute(select(s.attempts).join(s.exercises).where(and_(s.attempts.c.id == attempt_id,
                s.exercises.c.user_id == self.learner_id))).mappings().first()
            return api_row(row)

    def completed(self, exercise_id):
        with self.engine.connect() as db:
            return db.execute(select(s.attempts.c.id).join(s.exercises).where(and_(
                s.attempts.c.exercise_id == exercise_id, s.attempts.c.status == 'evaluated',
                s.exercises.c.user_id == self.learner_id))).scalar_one_or_none()

    def result(self, attempt_id):
        with self.engine.connect() as db:
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
        with self.engine.connect() as db:
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
        with self.engine.connect() as db:
            rows = db.execute(query.order_by(s.sessions.c.started_at.desc(), s.sessions.c.id.desc())).mappings().all()
            return [TrainingSession(id=row['id'], learner_id=row['user_id'], status=row['status'],
                started_at=aware(row['started_at']), completed_at=aware(row['completed_at']) if row['completed_at'] else None,
                starting_difficulty=row['starting_difficulty']) for row in rows]

    def session_exercises(self, session_id):
        with self.engine.connect() as db:
            rows = db.execute(select(s.exercises).join(s.sessions, s.exercises.c.session_id == s.sessions.c.id).where(and_(s.sessions.c.id == session_id,
                s.sessions.c.user_id == self.learner_id, s.exercises.c.user_id == self.learner_id))
                .order_by(s.exercises.c.created_at)).mappings().all()
            return [api_row(row) for row in rows]

    def conversation(self, exercise_id):
        with self.engine.connect() as db:
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

    def confirm_text(self, attempt_id, text):
        with self.transaction() as db:
            self.owned_attempt(db, attempt_id)
            db.execute(update(s.attempts).where(s.attempts.c.id == attempt_id).values(confirmed_text=text))

    def coach_audio(self, exercise_id, cue_id):
        with self.engine.connect() as db:
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
        with self.engine.connect() as db:
            return db.execute(select(s.audio_assets.c.data).where(and_(s.audio_assets.c.audio_name == audio_name,
                s.audio_assets.c.user_id == self.learner_id))).scalar_one_or_none()

    def owns_audio(self, audio_name):
        with self.engine.connect() as db:
            return db.execute(select(s.audio_assets.c.audio_name).where(and_(
                s.audio_assets.c.audio_name == audio_name, s.audio_assets.c.user_id == self.learner_id))).first() is not None

    def save_coach_audio(self, exercise_id, cue_id, audio_name, audio=None):
        with self.transaction() as db:
            self.owned_exercise(db, exercise_id)
            self.check_audio_owner(db, audio_name)
            self.upsert(db, s.coach_audio, dict(exercise_id=exercise_id, cue_id=cue_id, audio_name=audio_name), ['exercise_id', 'cue_id'])
            if audio is not None:
                self.save_audio(db, audio_name, audio)
