-- Grants and row-level-security policies for the Audli 2.0 API on a host where it runs as a non-owner role (Supabase).
-- Run once as the database owner AFTER migrations 009-011. Read, insert and update only: the API never removes 2.0 rows (account erasure
-- cascades from users through foreign keys, which run as the table owner).
BEGIN;
GRANT USAGE ON SCHEMA public TO audli_api_runtime;
GRANT SELECT, INSERT, UPDATE ON listening_lessons, listening_lesson_versions, listening_audio_variants, listening_exercises, listening_exercise_keys, listening_exercise_rubrics, listening_attempts, listening_answers, listening_responses, listening_spoken, listening_jobs, listening_ai_limits, listening_lesson_owners, listening_generation_requests, listening_generation_limits, listening_migrations TO audli_api_runtime;
REVOKE ALL ON listening_lessons, listening_lesson_versions, listening_audio_variants, listening_exercises, listening_exercise_keys, listening_exercise_rubrics, listening_attempts, listening_answers, listening_responses, listening_spoken, listening_jobs, listening_ai_limits, listening_lesson_owners, listening_generation_requests, listening_generation_limits, listening_migrations FROM anon;
REVOKE ALL ON listening_lessons, listening_lesson_versions, listening_audio_variants, listening_exercises, listening_exercise_keys, listening_exercise_rubrics, listening_attempts, listening_answers, listening_responses, listening_spoken, listening_jobs, listening_ai_limits, listening_lesson_owners, listening_generation_requests, listening_generation_limits, listening_migrations FROM authenticated;
REVOKE ALL ON listening_lessons, listening_lesson_versions, listening_audio_variants, listening_exercises, listening_exercise_keys, listening_exercise_rubrics, listening_attempts, listening_answers, listening_responses, listening_spoken, listening_jobs, listening_ai_limits, listening_lesson_owners, listening_generation_requests, listening_generation_limits, listening_migrations FROM service_role;
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_lessons;
CREATE POLICY audli_api_runtime_listening ON listening_lessons FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_lesson_versions;
CREATE POLICY audli_api_runtime_listening ON listening_lesson_versions FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_audio_variants;
CREATE POLICY audli_api_runtime_listening ON listening_audio_variants FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_exercises;
CREATE POLICY audli_api_runtime_listening ON listening_exercises FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_exercise_keys;
CREATE POLICY audli_api_runtime_listening ON listening_exercise_keys FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_exercise_rubrics;
CREATE POLICY audli_api_runtime_listening ON listening_exercise_rubrics FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_attempts;
CREATE POLICY audli_api_runtime_listening ON listening_attempts FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_answers;
CREATE POLICY audli_api_runtime_listening ON listening_answers FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_responses;
CREATE POLICY audli_api_runtime_listening ON listening_responses FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_spoken;
CREATE POLICY audli_api_runtime_listening ON listening_spoken FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_jobs;
CREATE POLICY audli_api_runtime_listening ON listening_jobs FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_ai_limits;
CREATE POLICY audli_api_runtime_listening ON listening_ai_limits FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_lesson_owners;
CREATE POLICY audli_api_runtime_listening ON listening_lesson_owners FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_generation_requests;
CREATE POLICY audli_api_runtime_listening ON listening_generation_requests FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_generation_limits;
CREATE POLICY audli_api_runtime_listening ON listening_generation_limits FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS audli_api_runtime_listening ON listening_migrations;
CREATE POLICY audli_api_runtime_listening ON listening_migrations FOR ALL TO audli_api_runtime USING (true) WITH CHECK (true);
DO $audli$
BEGIN
    IF to_regclass('public.listening_library_entries') IS NOT NULL THEN
        GRANT SELECT ON listening_library_entries TO audli_api_runtime;
        REVOKE ALL ON listening_library_entries FROM anon, authenticated, service_role;
        DROP POLICY IF EXISTS audli_api_runtime_listening_read ON listening_library_entries;
        CREATE POLICY audli_api_runtime_listening_read ON listening_library_entries FOR SELECT TO audli_api_runtime USING (true);
    END IF;
END
$audli$;
DO $audli$
BEGIN
    IF to_regclass('public.listening_daily_challenges') IS NOT NULL THEN
        GRANT SELECT ON listening_daily_challenges TO audli_api_runtime;
        REVOKE ALL ON listening_daily_challenges FROM anon, authenticated, service_role;
        DROP POLICY IF EXISTS audli_api_runtime_listening_read ON listening_daily_challenges;
        CREATE POLICY audli_api_runtime_listening_read ON listening_daily_challenges FOR SELECT TO audli_api_runtime USING (true);
    END IF;
END
$audli$;
DO $audli$
BEGIN
    IF to_regclass('public.listening_daily_control') IS NOT NULL THEN
        GRANT SELECT ON listening_daily_control TO audli_api_runtime;
        REVOKE ALL ON listening_daily_control FROM anon, authenticated, service_role;
        DROP POLICY IF EXISTS audli_api_runtime_listening_read ON listening_daily_control;
        CREATE POLICY audli_api_runtime_listening_read ON listening_daily_control FOR SELECT TO audli_api_runtime USING (true);
    END IF;
END
$audli$;
COMMIT;
