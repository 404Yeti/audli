-- Reference migration for a future PostgreSQL/Supabase repository implementation.
-- Not used by the SQLite prototype. Add authenticated ownership/RLS before exposure.
CREATE TABLE users (id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE learner_profiles (user_id uuid PRIMARY KEY REFERENCES users(id), data jsonb NOT NULL);
CREATE TABLE exercises (id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), content jsonb NOT NULL, difficulty jsonb NOT NULL, audio_name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE attempts (id uuid PRIMARY KEY, exercise_id uuid NOT NULL REFERENCES exercises(id), transcription jsonb NOT NULL, confirmed_text text, status text NOT NULL CHECK(status IN ('transcribed','evaluated')), created_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX one_evaluation_per_exercise ON attempts(exercise_id) WHERE status='evaluated';
CREATE TABLE evaluation_results (attempt_id uuid PRIMARY KEY REFERENCES attempts(id), data jsonb NOT NULL);
CREATE TABLE adaptation_events (id uuid PRIMARY KEY, attempt_id uuid UNIQUE NOT NULL REFERENCES attempts(id), data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
-- adaptation data includes harder/same/easier, teacher_decision (initially null),
-- previous score, one changed variable, old/new difficulty, focus, reason and policy thresholds.

-- V0.2 additive persistence; original V0.1 rows/results retain their original semantics.
CREATE TABLE conversations (exercise_id uuid PRIMARY KEY REFERENCES exercises(id), data jsonb NOT NULL);
CREATE TABLE coach_audio (exercise_id uuid NOT NULL REFERENCES exercises(id), cue_id text NOT NULL, audio_name text NOT NULL, PRIMARY KEY(exercise_id,cue_id));
