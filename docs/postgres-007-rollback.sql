-- Rollback for migration 007 (AUD-77). MANUAL USE ONLY, never run by automation.
-- Drops only advisory language-coaching rows (derived text; the saved answers, scores and history are untouched).
-- Run it only after the application is on a release that no longer needs schema 7 (the flag is off, or the bridge release
-- that accepts schema versions 6 and 7 is running). A release that predates the bridge rejects schema 7.
BEGIN;

DROP TABLE review_improvements;

DELETE FROM schema_migrations WHERE version = 7;

COMMIT;
