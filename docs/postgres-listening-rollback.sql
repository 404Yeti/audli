-- Audli 2.0 rollback: removes migrations 009-011 completely. DESTROYS 2.0 learner data. Read docs/LISTENING-CUTOVER.md first.
BEGIN;
SET LOCAL lock_timeout='10s';
DO $$ BEGIN
 IF coalesce(current_setting('audli.confirm_listening_rollback', true), '') <> 'yes' THEN
  RAISE EXCEPTION 'Refusing: run SET audli.confirm_listening_rollback = ''yes''; first, in this session';
 END IF;
 IF to_regclass('public.listening_attempts') IS NOT NULL
    AND (SELECT count(*) FROM public.listening_attempts) > 0
    AND coalesce(current_setting('audli.discard_learner_data', true), '') <> 'yes' THEN
  RAISE EXCEPTION 'Refusing: 2.0 attempts exist. To discard them run SET audli.discard_learner_data = ''yes''; first';
 END IF;
END $$;
DROP TABLE IF EXISTS listening_spoken;
DROP TABLE IF EXISTS listening_responses;
DROP TABLE IF EXISTS listening_answers;
DROP TABLE IF EXISTS listening_exercise_rubrics;
DROP TABLE IF EXISTS listening_exercise_keys;
DROP TABLE IF EXISTS listening_attempts;
DROP TABLE IF EXISTS listening_exercises;
DROP TABLE IF EXISTS listening_audio_variants;
DROP TABLE IF EXISTS listening_lesson_versions;
DROP TABLE IF EXISTS listening_jobs;
DROP TABLE IF EXISTS listening_migrations;
DROP TABLE IF EXISTS listening_lessons;
DROP TABLE IF EXISTS listening_ai_limits;
DROP FUNCTION IF EXISTS listening_guard_version();
DROP FUNCTION IF EXISTS listening_guard_children();
DROP FUNCTION IF EXISTS listening_guard_audio();
DROP FUNCTION IF EXISTS listening_guard_response();
DROP FUNCTION IF EXISTS listening_guard_attempt();
COMMIT;
