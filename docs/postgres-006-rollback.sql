-- Rollback for migration 006 (AUD-47). MANUAL USE ONLY, never run by automation.
--
-- Valid only while no lesson has status 'expired' and no ledger row records a void or a grace slot: those are written
-- only by enforce mode. scripts/migration006/sql/rollback_safety.sql evaluates this; scripts/migration006/07-rollback-production.sh
-- refuses to run when it does not report SAFE. After enforce has run, the rollback is AUDLI_LESSON_LIMIT_MODE=off, not this file.
-- Dropping the ledger loses only the 24-hour window state; historical lesson rows are never touched.
BEGIN;

DROP TABLE lesson_entitlements;
DROP FUNCTION lesson_entitlement_guard();

ALTER TABLE lesson_lifecycles DROP CONSTRAINT lesson_status_valid;
ALTER TABLE lesson_lifecycles ADD CONSTRAINT lesson_status_valid CHECK (status IN ('active','paused','completed'));
DROP INDEX one_open_lesson_per_learner;
CREATE UNIQUE INDEX one_open_lesson_per_learner ON lesson_lifecycles (user_id) WHERE status != 'completed';

DELETE FROM schema_migrations WHERE version = 6;

COMMIT;
