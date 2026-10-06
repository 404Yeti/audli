-- Migration 001: durable learner state. Run once as the backend database owner.

-- Generated from app/storage/schema.py; append later numbered migrations, never edit an applied baseline.

-- JSONB stores versioned domain snapshots; queryable fields and evidence have relational columns.

BEGIN;

CREATE TABLE schema_migrations (
	version INTEGER NOT NULL,
	applied_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (version)
);

CREATE TABLE users (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE audio_assets (
	audio_name TEXT NOT NULL,
	user_id UUID NOT NULL,
	data BYTEA NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (audio_name),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE learner_profiles (
	user_id UUID NOT NULL,
	data JSONB NOT NULL,
	name VARCHAR(80) NOT NULL,
	target_language VARCHAR(8) NOT NULL,
	goal TEXT NOT NULL,
	interests JSONB NOT NULL,
	target_situations JSONB NOT NULL,
	onboarding_status VARCHAR(20) NOT NULL,
	initial_listening_profile JSONB,
	overall FLOAT NOT NULL,
	main_idea FLOAT NOT NULL,
	details FLOAT NOT NULL,
	vocabulary FLOAT NOT NULL,
	inference FLOAT NOT NULL,
	natural_speed FLOAT,
	speech_rate FLOAT NOT NULL,
	duration_seconds INTEGER NOT NULL,
	vocabulary_level VARCHAR(2) NOT NULL,
	information_density INTEGER NOT NULL,
	speaker_count INTEGER NOT NULL,
	accent_complexity INTEGER NOT NULL,
	background_noise INTEGER NOT NULL,
	focus VARCHAR(20) NOT NULL,
	completed_attempts INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (user_id),
	CONSTRAINT onboarding_status_valid CHECK (onboarding_status IN ('not_started','in_progress','profile_saved','complete')),
	FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT profile_overall_bounds CHECK (overall IS NULL OR (overall >= 0 AND overall <= 1)),
	CONSTRAINT profile_main_idea_bounds CHECK (main_idea IS NULL OR (main_idea >= 0 AND main_idea <= 1)),
	CONSTRAINT profile_details_bounds CHECK (details IS NULL OR (details >= 0 AND details <= 1)),
	CONSTRAINT profile_vocabulary_bounds CHECK (vocabulary IS NULL OR (vocabulary >= 0 AND vocabulary <= 1)),
	CONSTRAINT profile_inference_bounds CHECK (inference IS NULL OR (inference >= 0 AND inference <= 1)),
	CONSTRAINT profile_natural_speed_bounds CHECK (natural_speed IS NULL OR (natural_speed >= 0 AND natural_speed <= 1)),
	CONSTRAINT difficulty_bounds CHECK (speech_rate BETWEEN 0.6 AND 1.15 AND duration_seconds BETWEEN 30 AND 90 AND information_density BETWEEN 1 AND 3),
	CONSTRAINT supported_difficulty CHECK (speaker_count=1 AND accent_complexity=1 AND background_noise=0),
	CONSTRAINT profile_level_count_valid CHECK (vocabulary_level IN ('A2','B1','B2','C1') AND completed_attempts >= 0)
);

CREATE TABLE training_sessions (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	status VARCHAR(12) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	starting_difficulty JSONB NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT session_status_valid CHECK (status IN ('active','completed')),
	CONSTRAINT session_completion_valid CHECK ((status='active' AND completed_at IS NULL) OR (status='completed' AND completed_at IS NOT NULL)),
	CONSTRAINT session_owner_key UNIQUE (id, user_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE exercises (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	session_id UUID NOT NULL,
	content JSONB NOT NULL,
	difficulty JSONB NOT NULL,
	audio_name TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT exercise_session_owner FOREIGN KEY(session_id, user_id) REFERENCES training_sessions (id, user_id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(session_id) REFERENCES training_sessions (id)
);

CREATE TABLE attempts (
	id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	transcription JSONB NOT NULL,
	recognized_text TEXT NOT NULL,
	confidence FLOAT,
	transcription_source VARCHAR(24) NOT NULL,
	confirmed_text TEXT,
	status VARCHAR(12) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT attempt_status_valid CHECK (status IN ('transcribed','evaluated')),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE TABLE coach_audio (
	exercise_id UUID NOT NULL,
	cue_id VARCHAR(80) NOT NULL,
	audio_name TEXT NOT NULL,
	PRIMARY KEY (exercise_id, cue_id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE TABLE conversation_followups (
	id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	position INTEGER NOT NULL,
	dimension VARCHAR(20) NOT NULL,
	unit_index INTEGER NOT NULL,
	question TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE TABLE adaptation_events (
	id UUID NOT NULL,
	attempt_id UUID NOT NULL,
	data JSONB NOT NULL,
	decision VARCHAR(8) NOT NULL,
	changed_variable VARCHAR(40),
	old_value JSONB,
	new_value JSONB,
	reason TEXT NOT NULL,
	previous_score FLOAT,
	policy_version VARCHAR(40) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (attempt_id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id)
);

CREATE TABLE conversation_turns (
	attempt_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	followup_id UUID,
	position INTEGER NOT NULL,
	confirmed_text TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (attempt_id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id),
	FOREIGN KEY(followup_id) REFERENCES conversation_followups (id)
);

CREATE TABLE conversations (
	exercise_id UUID NOT NULL,
	data JSONB NOT NULL,
	state VARCHAR(24) NOT NULL,
	pending_attempt_id UUID,
	active_followup_id UUID,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (exercise_id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id),
	FOREIGN KEY(pending_attempt_id) REFERENCES attempts (id)
);

CREATE TABLE evaluation_results (
	attempt_id UUID NOT NULL,
	data JSONB NOT NULL,
	version VARCHAR(12) NOT NULL,
	overall FLOAT,
	main_idea FLOAT,
	details FLOAT,
	vocabulary FLOAT,
	inference FLOAT,
	coverage FLOAT,
	coaching_outcome TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (attempt_id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id)
);

CREATE TABLE turn_assessments (
	attempt_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	data JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (attempt_id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE TABLE assessment_evidence (
	attempt_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	dimension VARCHAR(20) NOT NULL,
	unit_index INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	evidence TEXT NOT NULL,
	question TEXT NOT NULL,
	PRIMARY KEY (attempt_id, dimension, unit_index),
	CONSTRAINT evidence_status_valid CHECK (status IN ('demonstrated','partially_demonstrated','misunderstood','insufficient_evidence')),
	FOREIGN KEY(attempt_id) REFERENCES turn_assessments (attempt_id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE INDEX sessions_by_learner_time ON training_sessions (user_id, started_at);

CREATE INDEX exercises_by_learner_time ON exercises (user_id, created_at);

CREATE INDEX exercises_by_session ON exercises (session_id);

CREATE INDEX attempts_by_exercise ON attempts (exercise_id);

CREATE UNIQUE INDEX one_evaluation_per_exercise ON attempts (exercise_id) WHERE status = 'evaluated';

CREATE UNIQUE INDEX followups_by_exercise ON conversation_followups (exercise_id, position);

CREATE UNIQUE INDEX turns_by_exercise ON conversation_turns (exercise_id, position);

CREATE INDEX assessments_by_exercise ON turn_assessments (exercise_id, created_at);

ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE schema_migrations FROM PUBLIC;

ALTER TABLE users ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE users FROM PUBLIC;

ALTER TABLE audio_assets ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE audio_assets FROM PUBLIC;

ALTER TABLE learner_profiles ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE learner_profiles FROM PUBLIC;

ALTER TABLE training_sessions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE training_sessions FROM PUBLIC;

ALTER TABLE exercises ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE exercises FROM PUBLIC;

ALTER TABLE attempts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE attempts FROM PUBLIC;

ALTER TABLE coach_audio ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE coach_audio FROM PUBLIC;

ALTER TABLE conversation_followups ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE conversation_followups FROM PUBLIC;

ALTER TABLE adaptation_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE adaptation_events FROM PUBLIC;

ALTER TABLE conversation_turns ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE conversation_turns FROM PUBLIC;

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE conversations FROM PUBLIC;

ALTER TABLE evaluation_results ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE evaluation_results FROM PUBLIC;

ALTER TABLE turn_assessments ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE turn_assessments FROM PUBLIC;

ALTER TABLE assessment_evidence ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE assessment_evidence FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE schema_migrations, users, audio_assets, learner_profiles, training_sessions, exercises, attempts, coach_audio, conversation_followups, adaptation_events, conversation_turns, conversations, evaluation_results, turn_assessments, assessment_evidence FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE schema_migrations, users, audio_assets, learner_profiles, training_sessions, exercises, attempts, coach_audio, conversation_followups, adaptation_events, conversation_turns, conversations, evaluation_results, turn_assessments, assessment_evidence FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO schema_migrations(version, applied_at) VALUES (1, now());

COMMIT;
