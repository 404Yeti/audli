-- READ-ONLY. One line per check: PASS|name|detail or FAIL|name|detail. Requires lesson_entitlements to exist (postcheck.sh checks).
-- :'phase' is 'migration' (strict, immediately after applying) or 'observe' (later: the ledger may legitimately be newer than
-- the lesson rows because it keeps recording new starts, and may keep rows for learners whose lesson rows were cleaned up).
WITH latest AS (
  SELECT DISTINCT ON (user_id) user_id, id, started_at FROM lesson_lifecycles ORDER BY user_id, started_at DESC, id DESC
), checks(name, ok, detail) AS (
  SELECT 'schema_versions_1_to_6', coalesce((SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations) = '1,2,3,4,5,6', false),
         (SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations)
  UNION ALL SELECT 'version_6_recorded_once', (SELECT count(*) FROM schema_migrations WHERE version = 6) = 1, ''
  UNION ALL SELECT 'ledger_columns_exact',
         coalesce((SELECT array_agg(column_name::text ORDER BY column_name) FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'lesson_entitlements')
           = ARRAY['first_speech_failures','grace_actions','last_lesson_id','last_started_at','next_eligible_at','updated_at','user_id','voided_at']::text[], false),
         (SELECT string_agg(column_name::text, ',' ORDER BY column_name) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'lesson_entitlements')
  UNION ALL SELECT 'ledger_covers_every_learner_with_lessons',
         CASE WHEN :'phase' = 'migration' THEN (SELECT count(*) FROM lesson_entitlements) = (SELECT count(*) FROM latest)
              ELSE NOT EXISTS (SELECT 1 FROM latest l WHERE NOT EXISTS (SELECT 1 FROM lesson_entitlements e WHERE e.user_id = l.user_id)) END,
         (SELECT count(*) FROM lesson_entitlements)::text || ' ledger rows, ' || (SELECT count(*) FROM latest)::text || ' learners with lessons'
  UNION ALL SELECT 'backfill_equals_latest_lesson_start',
         CASE WHEN :'phase' = 'migration' THEN
              NOT EXISTS (SELECT 1 FROM latest l LEFT JOIN lesson_entitlements e USING (user_id)
                           WHERE e.user_id IS NULL OR e.last_started_at <> l.started_at OR e.last_lesson_id IS DISTINCT FROM l.id
                              OR e.next_eligible_at <> l.started_at + interval '24 hours' OR e.voided_at IS NOT NULL
                              OR e.first_speech_failures <> 0 OR e.grace_actions <> 0)
              ELSE NOT EXISTS (SELECT 1 FROM latest l JOIN lesson_entitlements e USING (user_id) WHERE e.last_started_at < l.started_at - interval '24 hours') END,
         (SELECT count(*) FROM latest l LEFT JOIN lesson_entitlements e USING (user_id)
           WHERE e.user_id IS NULL OR e.last_started_at <> l.started_at OR e.last_lesson_id IS DISTINCT FROM l.id)::text || ' differing learners (strict phase only)'
  UNION ALL SELECT 'window_is_exactly_24_hours_unless_voided',
         NOT EXISTS (SELECT 1 FROM lesson_entitlements WHERE voided_at IS NULL AND next_eligible_at <> last_started_at + interval '24 hours'), ''
  UNION ALL SELECT 'nothing_written_by_enforce_mode',
         NOT EXISTS (SELECT 1 FROM lesson_entitlements WHERE voided_at IS NOT NULL OR grace_actions <> 0 OR first_speech_failures <> 0)
           AND NOT EXISTS (SELECT 1 FROM lesson_lifecycles WHERE status = 'expired'), ''
  UNION ALL SELECT 'lesson_status_check_allows_expired',
         coalesce((SELECT pg_get_constraintdef(oid) LIKE '%expired%' FROM pg_constraint WHERE conname = 'lesson_status_valid' AND conrelid = 'lesson_lifecycles'::regclass), false),
         coalesce((SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'lesson_status_valid' AND conrelid = 'lesson_lifecycles'::regclass), 'missing')
  UNION ALL SELECT 'open_lesson_index_is_active_paused_only',
         coalesce((SELECT indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%active%' AND indexdef LIKE '%paused%' AND indexdef NOT LIKE '%completed%'
                     FROM pg_indexes WHERE indexname = 'one_open_lesson_per_learner'), false),
         coalesce((SELECT indexdef FROM pg_indexes WHERE indexname = 'one_open_lesson_per_learner'), 'missing')
  UNION ALL SELECT 'ledger_constraints_present',
         (SELECT count(*) FROM pg_constraint WHERE conrelid = 'lesson_entitlements'::regclass
             AND conname IN ('entitlement_window_ordered', 'entitlement_grace_bounded', 'lesson_entitlements_pkey')) = 3, ''
  UNION ALL SELECT 'ledger_has_no_foreign_key_to_lesson_rows',
         NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'lesson_entitlements'::regclass AND contype = 'f' AND confrelid <> 'users'::regclass), ''
  UNION ALL SELECT 'ledger_row_level_security_enabled',
         (SELECT relrowsecurity FROM pg_class WHERE oid = 'lesson_entitlements'::regclass), ''
  UNION ALL SELECT 'browser_roles_and_public_have_no_access',
         NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
                        AND has_table_privilege(rolname, 'public.lesson_entitlements', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
           AND NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants WHERE table_name = 'lesson_entitlements' AND grantee = 'PUBLIC'), ''
  UNION ALL SELECT 'guard_trigger_enabled_before_update_row',
         coalesce((SELECT tgenabled = 'O' AND (tgtype & 19) = 19 FROM pg_trigger WHERE tgrelid = 'lesson_entitlements'::regclass AND tgname = 'lesson_entitlement_guard' AND NOT tgisinternal), false), ''
  UNION ALL SELECT 'guard_function_present', EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'lesson_entitlement_guard'), ''
)
SELECT CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END || '|' || name || '|' || coalesce(detail, '') FROM checks;
