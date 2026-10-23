-- READ-ONLY. One line per check: PASS|FAIL|INFO, name, detail. Everything must be PASS before a backup is taken.
WITH facts AS (
  SELECT 'schema_versions_1_to_6' AS name,
         CASE WHEN (SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations) = '1,2,3,4,5,6' THEN 'PASS' ELSE 'FAIL' END AS verdict,
         coalesce((SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations), 'none') AS detail
  UNION ALL SELECT 'review_improvements_absent',
         CASE WHEN to_regclass('public.review_improvements') IS NULL THEN 'PASS' ELSE 'FAIL' END, coalesce(to_regclass('public.review_improvements')::text, 'absent')
  UNION ALL SELECT 'lesson_entitlements_present',
         CASE WHEN to_regclass('public.lesson_entitlements') IS NOT NULL THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'referenced_tables_present',
         CASE WHEN to_regclass('public.users') IS NOT NULL AND to_regclass('public.exercises') IS NOT NULL AND to_regclass('public.attempts') IS NOT NULL THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'no_long_running_transactions',
         CASE WHEN NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE xact_start IS NOT NULL AND pid <> pg_backend_pid()
                                  AND now() - xact_start > interval '30 seconds' AND state <> 'idle') THEN 'PASS' ELSE 'FAIL' END,
         (SELECT count(*)::text || ' active transactions older than 30 s' FROM pg_stat_activity WHERE xact_start IS NOT NULL AND pid <> pg_backend_pid() AND now() - xact_start > interval '30 seconds' AND state <> 'idle')
  UNION ALL SELECT 'server_version', 'INFO', current_setting('server_version')
  UNION ALL SELECT 'database_size', 'INFO', pg_size_pretty(pg_database_size(current_database()))
  UNION ALL SELECT 'learners_with_lessons', 'INFO', (SELECT count(DISTINCT user_id)::text FROM lesson_lifecycles)
  UNION ALL SELECT 'lessons_total', 'INFO', (SELECT count(*)::text FROM lesson_lifecycles)
  UNION ALL SELECT 'default_transaction_read_only', 'INFO', current_setting('default_transaction_read_only')
)
SELECT verdict || '|' || name || '|' || coalesce(detail, '') FROM facts;
