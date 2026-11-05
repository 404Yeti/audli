-- Migration 009: Audli 2.0 listening lessons (additive; no 1.x table is altered).
-- Requires migrations 001 through 007 and leaves schema_migrations untouched: 1.x reads max(version) >= 8 as the claims
-- schema, so 009 records itself in listening_migrations. Independent of 008. NEVER run at application startup.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.schema_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[1,2,3,4,5,6,7] <@ ARRAY(SELECT version FROM public.schema_migrations)) THEN
  RAISE EXCEPTION 'Expected explicit migrations 001 through 007';
 END IF;
 IF to_regclass('public.listening_migrations') IS NOT NULL OR to_regclass('public.listening_lessons') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 009 is already applied or partially present; review before retrying';
 END IF;
END $$;

CREATE TABLE listening_lessons (
	id UUID NOT NULL,
	slug VARCHAR(80) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (slug)
);

CREATE TABLE listening_migrations (
	version INTEGER NOT NULL,
	applied_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (version)
);

CREATE TABLE listening_lesson_versions (
	id UUID NOT NULL,
	lesson_id UUID NOT NULL,
	version_no INTEGER NOT NULL,
	status VARCHAR(10) NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	title VARCHAR(120) NOT NULL,
	topic VARCHAR(120) NOT NULL,
	language VARCHAR(8) NOT NULL,
	cefr VARCHAR(2) NOT NULL,
	listen_for VARCHAR(200) NOT NULL,
	speakers JSONB NOT NULL,
	sentences JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT listening_version_number UNIQUE (lesson_id, version_no),
	CONSTRAINT listening_version_content UNIQUE (lesson_id, content_hash),
	CONSTRAINT listening_version_status CHECK (status IN ('draft','published')),
	CONSTRAINT listening_version_published CHECK (version_no >= 1 AND ((status = 'published') = (published_at IS NOT NULL))),
	FOREIGN KEY(lesson_id) REFERENCES listening_lessons (id)
);

CREATE TABLE listening_audio_variants (
	id UUID NOT NULL,
	version_id UUID NOT NULL,
	accent VARCHAR(30) NOT NULL,
	voice VARCHAR(60) NOT NULL,
	provider VARCHAR(30) NOT NULL,
	object_key VARCHAR(200) NOT NULL,
	content_type VARCHAR(40) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size_bytes INTEGER NOT NULL,
	duration_ms INTEGER NOT NULL,
	alignment JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT listening_audio_variant UNIQUE (version_id, accent, voice),
	CONSTRAINT listening_audio_positive CHECK (size_bytes > 0 AND duration_ms > 0),
	FOREIGN KEY(version_id) REFERENCES listening_lesson_versions (id)
);

CREATE TABLE listening_exercises (
	id UUID NOT NULL,
	version_id UUID NOT NULL,
	position INTEGER NOT NULL,
	kind VARCHAR(20) NOT NULL,
	prompt TEXT NOT NULL,
	options JSONB NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT listening_exercise_position UNIQUE (version_id, position),
	CONSTRAINT listening_exercise_version UNIQUE (id, version_id),
	CONSTRAINT listening_exercise_kind CHECK (kind = 'multiple_choice' AND position >= 1),
	FOREIGN KEY(version_id) REFERENCES listening_lesson_versions (id)
);

CREATE TABLE listening_attempts (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	version_id UUID NOT NULL,
	audio_variant_id UUID NOT NULL,
	client_request_id VARCHAR(64) NOT NULL,
	status VARCHAR(12) NOT NULL,
	total INTEGER NOT NULL,
	correct INTEGER,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT listening_attempt_request UNIQUE (user_id, client_request_id),
	CONSTRAINT listening_attempt_version UNIQUE (id, version_id),
	CONSTRAINT listening_attempt_status CHECK (status IN ('in_progress','completed')),
	CONSTRAINT listening_attempt_state CHECK (total >= 1 AND ((status = 'completed') = (completed_at IS NOT NULL AND correct IS NOT NULL)) AND (correct IS NULL OR (correct >= 0 AND correct <= total))),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(version_id) REFERENCES listening_lesson_versions (id),
	FOREIGN KEY(audio_variant_id) REFERENCES listening_audio_variants (id)
);

CREATE TABLE listening_exercise_keys (
	exercise_id UUID NOT NULL,
	correct_option_id VARCHAR(32) NOT NULL,
	explanation TEXT NOT NULL,
	PRIMARY KEY (exercise_id),
	FOREIGN KEY(exercise_id) REFERENCES listening_exercises (id)
);

CREATE TABLE listening_responses (
	attempt_id UUID NOT NULL,
	exercise_id UUID NOT NULL,
	version_id UUID NOT NULL,
	option_id VARCHAR(32) NOT NULL,
	is_correct BOOLEAN NOT NULL,
	submitted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT listening_response_attempt FOREIGN KEY(attempt_id, version_id) REFERENCES listening_attempts (id, version_id) ON DELETE CASCADE,
	CONSTRAINT listening_response_exercise FOREIGN KEY(exercise_id, version_id) REFERENCES listening_exercises (id, version_id),
	CONSTRAINT listening_response_once UNIQUE (attempt_id, exercise_id)
);

CREATE INDEX listening_audio_by_object ON listening_audio_variants (object_key);

CREATE INDEX listening_attempts_by_learner ON listening_attempts (user_id, created_at);

CREATE UNIQUE INDEX listening_one_open_attempt ON listening_attempts (user_id, version_id) WHERE status = 'in_progress';

CREATE FUNCTION listening_guard_version() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF OLD.status = 'published' THEN RAISE EXCEPTION 'published listening versions are immutable' USING ERRCODE = '23000'; END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $f$;

CREATE TRIGGER listening_version_guard BEFORE UPDATE OR DELETE ON listening_lesson_versions FOR EACH ROW EXECUTE FUNCTION listening_guard_version();

CREATE FUNCTION listening_guard_children() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE vid uuid; st text;
BEGIN
    FOR i IN 0..1 LOOP
        IF (i = 0 AND TG_OP = 'INSERT') OR (i = 1 AND TG_OP = 'DELETE') THEN CONTINUE; END IF;
        IF TG_TABLE_NAME = 'listening_exercises' THEN
            vid := CASE WHEN i = 0 THEN OLD.version_id ELSE NEW.version_id END;
        ELSE
            SELECT version_id INTO vid FROM listening_exercises WHERE id = CASE WHEN i = 0 THEN OLD.exercise_id ELSE NEW.exercise_id END;
        END IF;
        SELECT status INTO st FROM listening_lesson_versions WHERE id = vid;
        IF st = 'published' THEN RAISE EXCEPTION 'published listening versions are immutable' USING ERRCODE = '23000'; END IF;
    END LOOP;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $f$;

CREATE TRIGGER listening_exercise_guard BEFORE INSERT OR UPDATE OR DELETE ON listening_exercises FOR EACH ROW EXECUTE FUNCTION listening_guard_children();

CREATE TRIGGER listening_key_guard BEFORE INSERT OR UPDATE OR DELETE ON listening_exercise_keys FOR EACH ROW EXECUTE FUNCTION listening_guard_children();

CREATE FUNCTION listening_guard_audio() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN RAISE EXCEPTION 'listening audio variants are append-only' USING ERRCODE = '23000'; END $f$;

CREATE TRIGGER listening_audio_guard BEFORE UPDATE OR DELETE ON listening_audio_variants FOR EACH ROW EXECUTE FUNCTION listening_guard_audio();

CREATE FUNCTION listening_guard_response() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN RAISE EXCEPTION 'listening answers are final' USING ERRCODE = '23000'; END $f$;

CREATE TRIGGER listening_response_guard BEFORE UPDATE ON listening_responses FOR EACH ROW EXECUTE FUNCTION listening_guard_response();

CREATE FUNCTION listening_guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF OLD.status = 'completed' OR NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.version_id IS DISTINCT FROM OLD.version_id
       OR NEW.audio_variant_id IS DISTINCT FROM OLD.audio_variant_id OR NEW.client_request_id IS DISTINCT FROM OLD.client_request_id
       OR NEW.total IS DISTINCT FROM OLD.total THEN
        RAISE EXCEPTION 'listening attempt identity is fixed and completed attempts are final' USING ERRCODE = '23000';
    END IF;
    RETURN NEW;
END $f$;

CREATE TRIGGER listening_attempt_guard BEFORE UPDATE ON listening_attempts FOR EACH ROW EXECUTE FUNCTION listening_guard_attempt();

ALTER TABLE listening_lessons ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_lessons FROM PUBLIC;

ALTER TABLE listening_migrations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_migrations FROM PUBLIC;

ALTER TABLE listening_lesson_versions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_lesson_versions FROM PUBLIC;

ALTER TABLE listening_audio_variants ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_audio_variants FROM PUBLIC;

ALTER TABLE listening_exercises ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_exercises FROM PUBLIC;

ALTER TABLE listening_attempts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_attempts FROM PUBLIC;

ALTER TABLE listening_exercise_keys ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_exercise_keys FROM PUBLIC;

ALTER TABLE listening_responses ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_responses FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE listening_lessons, listening_migrations, listening_lesson_versions, listening_audio_variants, listening_exercises, listening_attempts, listening_exercise_keys, listening_responses FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE listening_lessons, listening_migrations, listening_lesson_versions, listening_audio_variants, listening_exercises, listening_attempts, listening_exercise_keys, listening_responses FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO listening_migrations(version, applied_at) VALUES (9, now());

COMMIT;
