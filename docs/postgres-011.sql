-- Migration 011: Audli 2.0 AI spending ceiling and kill switch (one additive table).
-- Requires 010 (recorded in listening_migrations). Leaves schema_migrations untouched.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[9, 10] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009 and 010 first';
 END IF;
 IF 11 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_ai_limits') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 011 is already applied or partially present; review before retrying';
 END IF;
END $$;

CREATE TABLE listening_ai_limits (
	scope VARCHAR(16) NOT NULL,
	reserved_micro BIGINT NOT NULL,
	calls INTEGER NOT NULL,
	halted BOOLEAN NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (scope),
	CONSTRAINT listening_ai_limits_nonnegative CHECK (reserved_micro >= 0 AND calls >= 0)
);

ALTER TABLE listening_ai_limits ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_ai_limits FROM PUBLIC;

DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE listening_ai_limits FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE listening_ai_limits FROM authenticated;
    END IF;
END
$audli$;

INSERT INTO listening_migrations(version, applied_at) VALUES (11, now());

COMMIT;
