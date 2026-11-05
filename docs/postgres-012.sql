-- Migration 012: Audli 2.0 AI lesson generation (three additive tables: lesson owners, generation requests, generation limits).
-- Requires 011 (recorded in listening_migrations). Leaves schema_migrations untouched and alters no existing table.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010 and 011 first';
 END IF;
 IF 12 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_generation_requests') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 012 is already applied or partially present; review before retrying';
 END IF;
END $$;

CREATE TABLE listening_generation_limits (
	scope VARCHAR(10) NOT NULL,
	reserved_micro BIGINT NOT NULL,
	reserved_credits BIGINT NOT NULL,
	lessons INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (scope),
	CONSTRAINT listening_generation_limits_nonnegative CHECK (reserved_micro >= 0 AND reserved_credits >= 0 AND lessons >= 0)
);

CREATE TABLE listening_generation_requests (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	client_request_id VARCHAR(64) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	phase VARCHAR(16) NOT NULL,
	request_text VARCHAR(300),
	spec JSONB,
	question VARCHAR(300),
	title VARCHAR(120),
	lesson_id UUID,
	failure_code VARCHAR(40),
	reserved_credits INTEGER NOT NULL,
	actual_credits INTEGER,
	reserved_micro BIGINT NOT NULL,
	audio_key VARCHAR(160),
	audio_meta JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT listening_generation_request_once UNIQUE (user_id, client_request_id),
	CONSTRAINT listening_generation_kind CHECK (kind IN ('interpret','generate')),
	CONSTRAINT listening_generation_nonnegative CHECK (reserved_credits >= 0 AND reserved_micro >= 0),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(lesson_id) REFERENCES listening_lessons (id)
);

CREATE TABLE listening_lesson_owners (
	lesson_id UUID NOT NULL,
	user_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (lesson_id),
	FOREIGN KEY(lesson_id) REFERENCES listening_lessons (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX listening_generation_by_learner ON listening_generation_requests (user_id, created_at);

CREATE INDEX listening_lesson_owners_by_learner ON listening_lesson_owners (user_id);

ALTER TABLE listening_generation_limits ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_generation_limits FROM PUBLIC;

ALTER TABLE listening_generation_requests ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_generation_requests FROM PUBLIC;

ALTER TABLE listening_lesson_owners ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_lesson_owners FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE listening_generation_limits, listening_generation_requests, listening_lesson_owners FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE listening_generation_limits, listening_generation_requests, listening_lesson_owners FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO listening_migrations(version, applied_at) VALUES (12, now());

COMMIT;
