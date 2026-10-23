-- Rollback for migration 007 (AUD-77). MANUAL USE ONLY, never run by automation.
-- Drops only advisory language-coaching rows (derived text; the saved answers, scores and history are untouched).
-- Run it only after the application is on a release that no longer needs schema 7 (the flag is off, or the bridge release
-- that accepts schema versions 6 and 7 is running). A release that predates the bridge rejects schema 7.
-- It REFUSES to run while the table holds rows. To discard those rows deliberately, first take a backup of the table, then
-- authorize it in the same session before running this file:
--   SET audli.allow_destructive_rollback = 'DROP POPULATED review_improvements';
BEGIN;

DO $audli$
DECLARE kept bigint;
BEGIN
	SELECT count(*) INTO kept FROM review_improvements;
	IF kept > 0 AND coalesce(current_setting('audli.allow_destructive_rollback', true), '') <> 'DROP POPULATED review_improvements' THEN
		RAISE EXCEPTION USING MESSAGE = 'review_improvements holds ' || kept || ' rows; refusing to drop it without explicit destructive authorization (see the header of this file)';
	END IF;
END
$audli$;

DROP TABLE review_improvements;

DELETE FROM schema_migrations WHERE version = 7;

COMMIT;
