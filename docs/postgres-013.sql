-- Migration 013: Audli 2.0 account erasure support. Adds one helper function and replaces three guard functions (no new tables, no data change).
-- Requires 012 (lesson owners). A published lesson version, its exercises, keys, rubrics and audio can be deleted only inside a transaction that
-- sets audli.erasing_owner to the user who owns that lesson (listening_lesson_owners). Everything else stays immutable.

BEGIN;

SET LOCAL lock_timeout='5s';

LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;

DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11, 12] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010, 011 and 012 first';
 END IF;
 IF 13 IN (SELECT version FROM public.listening_migrations) THEN
  RAISE EXCEPTION 'Migration 013 is already applied; review before retrying';
 END IF;
END $$;

CREATE FUNCTION listening_erasing_lesson(lid uuid) RETURNS boolean LANGUAGE sql STABLE AS $f$
    SELECT EXISTS (SELECT 1 FROM listening_lesson_owners o
                   WHERE o.lesson_id = lid AND o.user_id::text = NULLIF(current_setting('audli.erasing_owner', true), ''))
$f$;

CREATE OR REPLACE FUNCTION listening_guard_version() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF OLD.status = 'published' AND NOT (TG_OP = 'DELETE' AND listening_erasing_lesson(OLD.lesson_id)) THEN
        RAISE EXCEPTION 'published listening versions are immutable' USING ERRCODE = '23000';
    END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $f$;

CREATE OR REPLACE FUNCTION listening_guard_children() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE vid uuid; st text; lid uuid;
BEGIN
    FOR i IN 0..1 LOOP
        IF (i = 0 AND TG_OP = 'INSERT') OR (i = 1 AND TG_OP = 'DELETE') THEN CONTINUE; END IF;
        IF TG_TABLE_NAME = 'listening_exercises' THEN
            vid := CASE WHEN i = 0 THEN OLD.version_id ELSE NEW.version_id END;
        ELSE
            SELECT version_id INTO vid FROM listening_exercises WHERE id = CASE WHEN i = 0 THEN OLD.exercise_id ELSE NEW.exercise_id END;
        END IF;
        SELECT status, lesson_id INTO st, lid FROM listening_lesson_versions WHERE id = vid;
        IF st = 'published' AND NOT (TG_OP = 'DELETE' AND listening_erasing_lesson(lid)) THEN
            RAISE EXCEPTION 'published listening versions are immutable' USING ERRCODE = '23000';
        END IF;
    END LOOP;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $f$;

CREATE OR REPLACE FUNCTION listening_guard_audio() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF TG_OP = 'DELETE' AND listening_erasing_lesson((SELECT v.lesson_id FROM listening_lesson_versions v WHERE v.id = OLD.version_id)) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'listening audio variants are append-only' USING ERRCODE = '23000';
END $f$;

INSERT INTO listening_migrations(version, applied_at) VALUES (13, now());

COMMIT;
