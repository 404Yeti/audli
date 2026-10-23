-- READ-ONLY. One line per fact: name|value. Baseline for migration 007: EVERY table in public (including lesson_entitlements, which the
-- migration 006 baseline excluded because it did not exist yet) plus checksums of the lesson and entitlement rows. Session time zone is
-- pinned so the text of timestamps is identical on any server.
SET TIME ZONE 'UTC';
SELECT 'rows:' || table_name || '|' || (xpath('/row/c/text()',
         query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text
  FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
 ORDER BY table_name;
SELECT 'lesson_status:' || status || '|' || count(*) FROM lesson_lifecycles GROUP BY status ORDER BY status;
SELECT 'learners_with_lessons|' || count(DISTINCT user_id) FROM lesson_lifecycles;
SELECT 'lesson_rows_md5|' || coalesce(md5(string_agg(id::text || ':' || user_id::text || ':' || status || ':' || revision::text || ':' ||
         started_at::text || ':' || md5(data::text), ',' ORDER BY id)), 'empty') FROM lesson_lifecycles;
SELECT 'entitlement_rows_md5|' || coalesce(md5(string_agg(user_id::text || ':' || extract(epoch from last_started_at)::text || ':' ||
         extract(epoch from next_eligible_at)::text || ':' || coalesce(last_lesson_id::text, '') || ':' || coalesce(extract(epoch from voided_at)::text, '') || ':' ||
         first_speech_failures::text || ':' || grace_actions::text || ':' || extract(epoch from updated_at)::text, ',' ORDER BY user_id)), 'empty') FROM lesson_entitlements;
SELECT 'schema_versions|' || coalesce(string_agg(version::text, ',' ORDER BY version), '') FROM schema_migrations;
