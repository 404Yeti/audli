-- READ-ONLY. One line per check: PASS|FAIL|INFO, name, detail. Everything must be PASS before a backup is taken.
WITH facts AS (
  SELECT 'schema_versions_1_to_5' AS name,
         CASE WHEN (SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations) = '1,2,3,4,5' THEN 'PASS' ELSE 'FAIL' END AS verdict,
         coalesce((SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations), 'none') AS detail
  UNION ALL SELECT 'ledger_table_absent',
         CASE WHEN to_regclass('public.lesson_entitlements') IS NULL THEN 'PASS' ELSE 'FAIL' END, coalesce(to_regclass('public.lesson_entitlements')::text, 'absent')
  UNION ALL SELECT 'guard_function_absent',
         CASE WHEN NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'lesson_entitlement_guard') THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'lesson_status_check_is_original',
         CASE WHEN (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'lesson_status_valid' AND conrelid = 'lesson_lifecycles'::regclass) NOT LIKE '%expired%' THEN 'PASS' ELSE 'FAIL' END,
         coalesce((SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'lesson_status_valid' AND conrelid = 'lesson_lifecycles'::regclass), 'missing')
  UNION ALL SELECT 'open_lesson_index_is_original',
         CASE WHEN (SELECT indexdef FROM pg_indexes WHERE indexname = 'one_open_lesson_per_learner') LIKE '%<> ''completed''%' THEN 'PASS' ELSE 'FAIL' END,
         coalesce((SELECT indexdef FROM pg_indexes WHERE indexname = 'one_open_lesson_per_learner'), 'missing')
  UNION ALL SELECT 'no_expired_lessons',
         CASE WHEN NOT EXISTS (SELECT 1 FROM lesson_lifecycles WHERE status = 'expired') THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'at_most_one_open_lesson_per_learner',
         CASE WHEN NOT EXISTS (SELECT 1 FROM lesson_lifecycles WHERE status <> 'completed' GROUP BY user_id HAVING count(*) > 1) THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'no_long_running_transactions',
         CASE WHEN NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE xact_start IS NOT NULL AND pid <> pg_backend_pid()
                                  AND now() - xact_start > interval '30 seconds' AND state <> 'idle') THEN 'PASS' ELSE 'FAIL' END,
         (SELECT count(*)::text || ' active transactions older than 30 s' FROM pg_stat_activity WHERE xact_start IS NOT NULL AND pid <> pg_backend_pid() AND now() - xact_start > interval '30 seconds' AND state <> 'idle')
  UNION ALL SELECT 'no_blocking_locks_on_lessons',
         CASE WHEN NOT EXISTS (SELECT 1 FROM pg_locks WHERE relation = 'lesson_lifecycles'::regclass AND mode IN ('AccessExclusiveLock', 'ExclusiveLock', 'ShareRowExclusiveLock') AND granted) THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'users_table_present', CASE WHEN to_regclass('public.users') IS NOT NULL THEN 'PASS' ELSE 'FAIL' END, ''
  UNION ALL SELECT 'server_version', 'INFO', current_setting('server_version')
  UNION ALL SELECT 'database_size', 'INFO', pg_size_pretty(pg_database_size(current_database()))
  UNION ALL SELECT 'learners_with_lessons', 'INFO', (SELECT count(DISTINCT user_id)::text FROM lesson_lifecycles)
  UNION ALL SELECT 'lessons_total', 'INFO', (SELECT count(*)::text FROM lesson_lifecycles)
  UNION ALL SELECT 'default_transaction_read_only', 'INFO', current_setting('default_transaction_read_only')
)
SELECT verdict || '|' || name || '|' || coalesce(detail, '') FROM facts;
