"""Shared relational schema; JSON documents preserve versioned domain snapshots."""
from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, Float,
                        DateTime, JSON, LargeBinary, ForeignKey, Index, CheckConstraint,
                        ForeignKeyConstraint, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()
document = JSON().with_variant(JSONB(), 'postgresql')
identifier = String(36).with_variant(UUID(as_uuid=False), 'postgresql')
timestamp = DateTime(timezone=True)

versions = Table('schema_migrations', metadata,
    Column('version', Integer, primary_key=True, autoincrement=False), Column('applied_at', timestamp, nullable=False))
users = Table('users', metadata, Column('id', identifier, primary_key=True),
    Column('created_at', timestamp, nullable=False))
profiles = Table('learner_profiles', metadata,
    Column('user_id', identifier, ForeignKey('users.id'), primary_key=True),
    Column('data', document, nullable=False),
    Column('name', String(80)), Column('target_language', String(8)), Column('goal', Text),
    Column('interests', document), Column('target_situations', document),
    Column('onboarding_status', String(20)), Column('initial_listening_profile', document),
    Column('overall', Float), Column('main_idea', Float), Column('details', Float),
    Column('vocabulary', Float), Column('inference', Float), Column('natural_speed', Float),
    Column('speech_rate', Float), Column('duration_seconds', Integer), Column('vocabulary_level', String(2)),
    Column('information_density', Integer), Column('speaker_count', Integer),
    Column('accent_complexity', Integer), Column('background_noise', Integer),
    Column('focus', String(20)), Column('completed_attempts', Integer), Column('updated_at', timestamp),
    CheckConstraint("onboarding_status IN ('not_started','in_progress','profile_saved','complete')", name='onboarding_status_valid'))
for dimension in ('overall', 'main_idea', 'details', 'vocabulary', 'inference', 'natural_speed'):
    profiles.append_constraint(CheckConstraint(f'{dimension} IS NULL OR ({dimension} >= 0 AND {dimension} <= 1)', name=f'profile_{dimension}_bounds'))
profiles.append_constraint(CheckConstraint('speech_rate BETWEEN 0.6 AND 1.15 AND duration_seconds BETWEEN 30 AND 90 AND information_density BETWEEN 1 AND 3', name='difficulty_bounds'))
profiles.append_constraint(CheckConstraint('speaker_count=1 AND accent_complexity=1 AND background_noise=0', name='supported_difficulty'))
profiles.append_constraint(CheckConstraint("vocabulary_level IN ('A2','B1','B2','C1') AND completed_attempts >= 0", name='profile_level_count_valid'))
sessions = Table('training_sessions', metadata,
    Column('id', identifier, primary_key=True), Column('user_id', identifier, ForeignKey('users.id'), nullable=False),
    Column('status', String(12), nullable=False), Column('started_at', timestamp, nullable=False),
    Column('completed_at', timestamp), Column('starting_difficulty', document, nullable=False),
    CheckConstraint("status IN ('active','completed')", name='session_status_valid'),
    CheckConstraint("(status='active' AND completed_at IS NULL) OR (status='completed' AND completed_at IS NOT NULL)", name='session_completion_valid'),
    UniqueConstraint('id', 'user_id', name='session_owner_key'))
exercises = Table('exercises', metadata,
    Column('id', identifier, primary_key=True), Column('user_id', identifier, ForeignKey('users.id'), nullable=False),
    Column('session_id', identifier, ForeignKey('training_sessions.id')),
    Column('content', document, nullable=False), Column('difficulty', document, nullable=False),
    Column('audio_name', Text, nullable=False), Column('created_at', timestamp, nullable=False),
    ForeignKeyConstraint(['session_id', 'user_id'], ['training_sessions.id', 'training_sessions.user_id'], name='exercise_session_owner'))
attempts = Table('attempts', metadata,
    Column('id', identifier, primary_key=True), Column('exercise_id', identifier, ForeignKey('exercises.id'), nullable=False),
    Column('transcription', document, nullable=False), Column('recognized_text', Text),
    Column('confidence', Float), Column('transcription_source', String(24)),
    Column('confirmed_text', Text), Column('status', String(12), nullable=False),
    Column('created_at', timestamp, nullable=False),
    CheckConstraint("status IN ('transcribed','evaluated')", name='attempt_status_valid'))
evaluations = Table('evaluation_results', metadata,
    Column('attempt_id', identifier, ForeignKey('attempts.id'), primary_key=True),
    Column('data', document, nullable=False), Column('version', String(12)),
    Column('overall', Float), Column('main_idea', Float), Column('details', Float),
    Column('vocabulary', Float), Column('inference', Float), Column('coverage', Float),
    Column('coaching_outcome', Text), Column('created_at', timestamp))
events = Table('adaptation_events', metadata,
    Column('id', identifier, primary_key=True), Column('attempt_id', identifier, ForeignKey('attempts.id'), unique=True, nullable=False),
    Column('data', document, nullable=False), Column('decision', String(8)), Column('changed_variable', String(40)),
    Column('old_value', document), Column('new_value', document), Column('reason', Text),
    Column('previous_score', Float), Column('policy_version', String(40)), Column('created_at', timestamp, nullable=False))
conversations = Table('conversations', metadata,
    Column('exercise_id', identifier, ForeignKey('exercises.id'), primary_key=True),
    Column('data', document, nullable=False), Column('state', String(24)),
    Column('pending_attempt_id', identifier, ForeignKey('attempts.id')),
    Column('active_followup_id', identifier), Column('updated_at', timestamp))
assessments = Table('turn_assessments', metadata,
    Column('attempt_id', identifier, ForeignKey('attempts.id'), primary_key=True),
    Column('exercise_id', identifier, ForeignKey('exercises.id'), nullable=False),
    Column('data', document, nullable=False), Column('created_at', timestamp, nullable=False))
followups = Table('conversation_followups', metadata,
    Column('id', identifier, primary_key=True), Column('exercise_id', identifier, ForeignKey('exercises.id'), nullable=False),
    Column('position', Integer, nullable=False), Column('dimension', String(20), nullable=False),
    Column('unit_index', Integer, nullable=False), Column('question', Text, nullable=False),
    Column('created_at', timestamp, nullable=False))
turns = Table('conversation_turns', metadata,
    Column('attempt_id', identifier, ForeignKey('attempts.id'), primary_key=True),
    Column('exercise_id', identifier, ForeignKey('exercises.id'), nullable=False),
    Column('followup_id', identifier, ForeignKey('conversation_followups.id')),
    Column('position', Integer, nullable=False), Column('confirmed_text', Text, nullable=False),
    Column('created_at', timestamp, nullable=False))
evidence = Table('assessment_evidence', metadata,
    Column('attempt_id', identifier, ForeignKey('turn_assessments.attempt_id'), primary_key=True),
    Column('exercise_id', identifier, ForeignKey('exercises.id'), nullable=False),
    Column('dimension', String(20), primary_key=True), Column('unit_index', Integer, primary_key=True),
    Column('status', String(24), nullable=False), Column('evidence', Text, nullable=False),
    Column('question', Text, nullable=False),
    CheckConstraint("status IN ('demonstrated','partially_demonstrated','misunderstood','insufficient_evidence')", name='evidence_status_valid'))
coach_audio = Table('coach_audio', metadata,
    Column('exercise_id', identifier, ForeignKey('exercises.id'), primary_key=True),
    Column('cue_id', String(80), primary_key=True), Column('audio_name', Text, nullable=False))
audio_assets = Table('audio_assets', metadata,
    Column('audio_name', Text, primary_key=True), Column('user_id', identifier, ForeignKey('users.id'), nullable=False),
    Column('data', LargeBinary, nullable=False), Column('created_at', timestamp, nullable=False))

Index('sessions_by_learner_time', sessions.c.user_id, sessions.c.started_at)
Index('exercises_by_learner_time', exercises.c.user_id, exercises.c.created_at)
Index('exercises_by_session', exercises.c.session_id)
Index('attempts_by_exercise', attempts.c.exercise_id)
Index('followups_by_exercise', followups.c.exercise_id, followups.c.position, unique=True)
Index('turns_by_exercise', turns.c.exercise_id, turns.c.position, unique=True)
Index('assessments_by_exercise', assessments.c.exercise_id, assessments.c.created_at)
Index('one_evaluation_per_exercise', attempts.c.exercise_id, unique=True,
    sqlite_where=attempts.c.status == 'evaluated', postgresql_where=attempts.c.status == 'evaluated')

# Required projections in new databases. Existing SQLite columns are added as
# nullable then backfilled atomically; domain validation remains their boundary.
for table, optional in (
    (profiles, {'natural_speed', 'initial_listening_profile'}),
    (exercises, set()),
    (attempts, {'confirmed_text', 'confidence'}),
    (evaluations, {'overall', 'main_idea', 'details', 'vocabulary', 'inference', 'coverage'}),
    (events, {'changed_variable', 'old_value', 'new_value', 'previous_score'}),
    (conversations, {'pending_attempt_id', 'active_followup_id'}),
):
    for column in table.columns:
        if column.name not in optional:
            column.nullable = False
