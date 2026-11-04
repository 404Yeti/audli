-- Migration 010: Audli 2.0 four-exercise journey (rubrics, answers, spoken responses, durable jobs).
-- Requires 009 (recorded in listening_migrations). Widens listening_exercises.kind; no 1.x table is touched and
-- schema_migrations is untouched. NEVER run at application startup.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[9] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migration 009 first';
 END IF;
 IF 10 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_jobs') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 010 is already applied or partially present; review before retrying';
 END IF;
END $$;

ALTER TABLE listening_exercises DROP CONSTRAINT listening_exercise_kind;

ALTER TABLE listening_exercises ADD CONSTRAINT listening_exercise_kind CHECK (kind IN ('multiple_choice','spoken','fill_blank','sequencing') AND position >= 1);

CREATE TABLE listening_jobs (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	kind VARCHAR(20) NOT NULL,
	dedupe_key VARCHAR(120) NOT NULL,
	payload JSONB NOT NULL,
	status VARCHAR(10) NOT NULL,
	attempts INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	run_after TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_owner VARCHAR(64),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	fence INTEGER NOT NULL,
	last_error VARCHAR(60),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT listening_job_status CHECK (status IN ('queued','running','succeeded','failed')),
	CONSTRAINT listening_job_counts CHECK (attempts >= 0 AND max_attempts >= 1 AND fence >= 0),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (dedupe_key)
);

CREATE TABLE listening_exercise_rubrics (
	exercise_id UUID NOT NULL,
	data JSONB NOT NULL,
	PRIMARY KEY (exercise_id),
	FOREIGN KEY(exercise_id) REFERENCES listening_exercises (id)
);

CREATE TABLE listening_answers (
	attempt_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	version_id UUID NOT NULL,
	answer JSONB NOT NULL,
	is_correct BOOLEAN NOT NULL,
	submitted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT listening_answer_attempt FOREIGN KEY(attempt_id, version_id) REFERENCES listening_attempts (id, version_id) ON DELETE CASCADE,
	CONSTRAINT listening_answer_exercise FOREIGN KEY(exercise_id, version_id) REFERENCES listening_exercises (id, version_id),
	CONSTRAINT listening_answer_once UNIQUE (attempt_id, exercise_id)
);

CREATE TABLE listening_spoken (
	attempt_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	version_id UUID NOT NULL,
	status VARCHAR(10) NOT NULL,
	generation INTEGER NOT NULL,
	upload_id VARCHAR(64),
	recording_key VARCHAR(200),
	recording_type VARCHAR(40),
	transcript TEXT,
	assessment JSONB,
	failure_code VARCHAR(40),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT listening_spoken_pk PRIMARY KEY (attempt_id, exercise_id),
	CONSTRAINT listening_spoken_attempt FOREIGN KEY(attempt_id, version_id) REFERENCES listening_attempts (id, version_id) ON DELETE CASCADE,
	CONSTRAINT listening_spoken_exercise FOREIGN KEY(exercise_id, version_id) REFERENCES listening_exercises (id, version_id),
	CONSTRAINT listening_spoken_status CHECK (status IN ('skipped','receiving','pending','assessed','failed')),
	CONSTRAINT listening_spoken_generation CHECK (generation >= 0)
);

CREATE INDEX listening_jobs_ready ON listening_jobs (status, run_after);

CREATE INDEX listening_spoken_by_status ON listening_spoken (status, updated_at);

CREATE TRIGGER listening_rubric_guard BEFORE INSERT OR UPDATE OR DELETE ON listening_exercise_rubrics FOR EACH ROW EXECUTE FUNCTION listening_guard_children();

CREATE TRIGGER listening_answer_guard BEFORE UPDATE ON listening_answers FOR EACH ROW EXECUTE FUNCTION listening_guard_response();

ALTER TABLE listening_jobs ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_jobs FROM PUBLIC;

ALTER TABLE listening_exercise_rubrics ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_exercise_rubrics FROM PUBLIC;

ALTER TABLE listening_answers ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_answers FROM PUBLIC;

ALTER TABLE listening_spoken ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_spoken FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE listening_jobs, listening_exercise_rubrics, listening_answers, listening_spoken FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE listening_jobs, listening_exercise_rubrics, listening_answers, listening_spoken FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO listening_migrations(version, applied_at) VALUES (10, now());

COMMIT;
