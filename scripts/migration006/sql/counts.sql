-- READ-ONLY. One line per fact: name|value. Used as the before/after baseline for migration 006.
SELECT 'rows:' || table_name || '|' || (xpath('/row/c/text()',
         query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text
  FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'lesson_entitlements'
 ORDER BY table_name;
SELECT 'lesson_status:' || status || '|' || count(*) FROM lesson_lifecycles GROUP BY status ORDER BY status;
SELECT 'learners_with_lessons|' || count(DISTINCT user_id) FROM lesson_lifecycles;
SELECT 'lesson_rows_md5|' || coalesce(md5(string_agg(id::text || ':' || user_id::text || ':' || status || ':' || revision::text || ':' ||
         started_at::text || ':' || md5(data::text), ',' ORDER BY id)), 'empty') FROM lesson_lifecycles;
SELECT 'schema_versions|' || coalesce(string_agg(version::text, ',' ORDER BY version), '') FROM schema_migrations;
