-- Migration 014: Audli 2.0 curated library (one additive table: listening_library_entries).
-- Requires 009-012 (recorded in listening_migrations). Leaves schema_migrations untouched and alters no existing table. Independent of 013.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11, 12] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010, 011 and 012 first';
 END IF;
 IF 14 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_library_entries') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 014 is already applied or partially present; review before retrying';
 END IF;
END $$;

CREATE TABLE listening_library_entries (
	lesson_id UUID NOT NULL,
	version_id UUID NOT NULL,
	state VARCHAR(12) NOT NULL,
	category VARCHAR(20) NOT NULL,
	curator_id UUID,
	reviewer VARCHAR(80),
	review_note VARCHAR(500),
	reviewed_digest VARCHAR(64),
	approved_at TIMESTAMP WITH TIME ZONE,
	published_at TIMESTAMP WITH TIME ZONE,
	unpublished_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (lesson_id),
	CONSTRAINT listening_library_state CHECK (state IN ('draft','approved','published','unpublished')),
	CONSTRAINT listening_library_reviewed CHECK (state NOT IN ('approved','published') OR (reviewer IS NOT NULL AND approved_at IS NOT NULL AND reviewed_digest IS NOT NULL)),
	CONSTRAINT listening_library_published CHECK (state <> 'published' OR published_at IS NOT NULL),
	FOREIGN KEY(lesson_id) REFERENCES listening_lessons (id) ON DELETE CASCADE,
	FOREIGN KEY(version_id) REFERENCES listening_lesson_versions (id) ON DELETE CASCADE,
	FOREIGN KEY(curator_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX listening_library_by_curator ON listening_library_entries (curator_id);

CREATE INDEX listening_library_by_state ON listening_library_entries (state);

ALTER TABLE listening_library_entries ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_library_entries FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE listening_library_entries FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE listening_library_entries FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO listening_migrations(version, applied_at) VALUES (14, now());

COMMIT;
