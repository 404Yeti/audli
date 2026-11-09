-- Migration 015: Audli 2.0 daily challenge (two additive tables: listening_daily_challenges, listening_daily_control).
-- Requires 009-012 and 014 (recorded in listening_migrations). Leaves schema_migrations untouched and alters no existing table.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11, 12, 14] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010, 011, 012 and 014 first';
 END IF;
 IF 15 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_daily_challenges') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 015 is already applied or partially present; review before retrying';
 END IF;
END $$;

CREATE TABLE listening_daily_control (
	scope VARCHAR(10) NOT NULL,
	paused BOOLEAN NOT NULL,
	note VARCHAR(200),
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (scope)
);

CREATE TABLE listening_daily_challenges (
	challenge_date VARCHAR(10) NOT NULL,
	state VARCHAR(12) NOT NULL,
	attempts INTEGER NOT NULL,
	plan JSONB,
	request_id UUID,
	lesson_id UUID,
	version_id UUID,
	fingerprint VARCHAR(64),
	topic_key VARCHAR(60),
	category VARCHAR(30),
	rejected JSONB NOT NULL,
	gate_report JSONB,
	events JSONB NOT NULL,
	failure_code VARCHAR(40),
	review_micro BIGINT NOT NULL,
	lease_owner VARCHAR(60),
	lease_until TIMESTAMP WITH TIME ZONE,
	published_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (challenge_date),
	CONSTRAINT listening_daily_state CHECK (state IN ('planned','generating','built','scheduled','active','failed')),
	CONSTRAINT listening_daily_nonnegative CHECK (attempts >= 0 AND review_micro >= 0),
	CONSTRAINT listening_daily_validated CHECK (state NOT IN ('scheduled','active') OR (lesson_id IS NOT NULL AND fingerprint IS NOT NULL)),
	CONSTRAINT listening_daily_published CHECK (state <> 'active' OR published_at IS NOT NULL),
	UNIQUE (lesson_id),
	FOREIGN KEY(lesson_id) REFERENCES listening_lessons (id),
	UNIQUE (fingerprint)
);

CREATE INDEX listening_daily_by_state ON listening_daily_challenges (state);

ALTER TABLE listening_daily_control ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_daily_control FROM PUBLIC;

ALTER TABLE listening_daily_challenges ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE listening_daily_challenges FROM PUBLIC;

DO $audli$
DECLARE client text; tbl text;
BEGIN
    FOREACH client IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = client) THEN
            EXECUTE 'REVOKE ALL ON TABLE listening_daily_control, listening_daily_challenges FROM ' || quote_ident(client);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audli_api_runtime') THEN
        GRANT SELECT ON TABLE listening_daily_control, listening_daily_challenges TO audli_api_runtime;
        FOREACH tbl IN ARRAY ARRAY['listening_daily_challenges', 'listening_daily_control'] LOOP
            EXECUTE format('CREATE POLICY audli_api_runtime_listening_read ON %I FOR SELECT TO audli_api_runtime USING (true)', tbl);
        END LOOP;
    END IF;
END
$audli$;

DO $audli$
DECLARE bad text;
BEGIN
    SELECT string_agg(c.relname || '=' || coalesce(r.rolname, 'PUBLIC') || ':' || a.privilege_type, ', ') INTO bad
      FROM pg_class c
      CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
      LEFT JOIN pg_roles r ON r.oid = a.grantee
     WHERE c.oid IN ('public.listening_daily_challenges'::regclass, 'public.listening_daily_control'::regclass) AND a.grantee <> c.relowner
       AND NOT (r.rolname = 'audli_api_runtime' AND a.privilege_type = 'SELECT');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION USING MESSAGE = 'Refusing: unexpected privileges: ' || bad;
    END IF;
END
$audli$;

INSERT INTO listening_migrations(version, applied_at) VALUES (15, now());

COMMIT;
