-- Migration 006: durable daily lesson entitlement and the expired lesson status.

BEGIN;

CREATE TABLE lesson_entitlements (
	user_id UUID NOT NULL,
	last_started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	next_eligible_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_lesson_id UUID,
	voided_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (user_id),
	CONSTRAINT entitlement_window_ordered CHECK (next_eligible_at >= last_started_at),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

ALTER TABLE lesson_entitlements ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE lesson_entitlements FROM PUBLIC;

DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON lesson_entitlements FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON lesson_entitlements FROM authenticated; END IF; END $audli$;

-- Existing learners keep their latest real lesson start; historical lesson rows are not altered.
INSERT INTO lesson_entitlements (user_id, last_started_at, next_eligible_at, last_lesson_id, updated_at)
SELECT DISTINCT ON (user_id) user_id, started_at, started_at + interval '24 hours', id, now()
FROM lesson_lifecycles ORDER BY user_id, started_at DESC, id DESC;

-- Backstop for the application's atomic claim: no code path may start a new window early.
-- It compares only values on the row itself, so it does not depend on any clock.
CREATE FUNCTION lesson_entitlement_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
    IF NEW.last_started_at IS DISTINCT FROM OLD.last_started_at THEN
        IF NEW.last_started_at < OLD.next_eligible_at THEN
            RAISE EXCEPTION 'lesson_cooldown_active' USING ERRCODE = 'P0001';
        END IF;
        IF NEW.next_eligible_at <> NEW.last_started_at + interval '24 hours' THEN
            RAISE EXCEPTION 'lesson_window_invalid' USING ERRCODE = 'P0001';
        END IF;
    ELSIF NEW.next_eligible_at > OLD.next_eligible_at THEN
        RAISE EXCEPTION 'lesson_window_extension_refused' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END
$guard$;

CREATE TRIGGER lesson_entitlement_guard BEFORE UPDATE ON lesson_entitlements
    FOR EACH ROW EXECUTE FUNCTION lesson_entitlement_guard();

-- An expired lesson keeps its partial work but is neither open nor completed.
ALTER TABLE lesson_lifecycles DROP CONSTRAINT lesson_status_valid;
ALTER TABLE lesson_lifecycles ADD CONSTRAINT lesson_status_valid CHECK (status IN ('active','paused','completed','expired'));
DROP INDEX one_open_lesson_per_learner;
CREATE UNIQUE INDEX one_open_lesson_per_learner ON lesson_lifecycles (user_id) WHERE status IN ('active','paused');

INSERT INTO schema_migrations(version, applied_at) VALUES (6, now());

COMMIT;
