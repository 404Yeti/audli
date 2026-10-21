-- READ-ONLY. Decides whether docs/postgres-006-rollback.sql is still safe. One line per fact (FACT|name|value), then one verdict
-- line (SAFE|... or UNSAFE|reasons). Rolling back drops the ledger and the 'expired' status; both only matter once enforce mode ran.
WITH facts AS (
  SELECT (SELECT count(*) FROM lesson_lifecycles WHERE status = 'expired') AS expired_lessons,
         (SELECT count(*) FROM lesson_entitlements WHERE voided_at IS NOT NULL) AS voided_allowances,
         (SELECT coalesce(sum(grace_actions), 0) FROM lesson_entitlements) AS grace_slots_used,
         (SELECT coalesce(sum(first_speech_failures), 0) FROM lesson_entitlements) AS first_speech_failures,
         (SELECT max(version) FROM schema_migrations) AS max_schema_version,
         (SELECT count(*) FROM lesson_entitlements) AS ledger_rows,
         (SELECT count(*) FROM lesson_entitlements e WHERE NOT EXISTS (SELECT 1 FROM lesson_lifecycles l WHERE l.user_id = e.user_id)) AS ledger_rows_without_lesson_rows
), verdict AS (
  SELECT *, concat_ws('; ',
           CASE WHEN expired_lessons > 0 THEN expired_lessons || ' expired lesson(s): the old status check would reject them' END,
           CASE WHEN voided_allowances > 0 THEN voided_allowances || ' voided allowance(s) recorded (enforce mode ran)' END,
           CASE WHEN grace_slots_used > 0 THEN grace_slots_used || ' grace slot(s) used (enforce mode ran)' END,
           CASE WHEN first_speech_failures > 0 THEN first_speech_failures || ' first-speech failure(s) counted (enforce mode ran)' END,
           CASE WHEN max_schema_version <> 6 THEN 'schema version is ' || max_schema_version || ', not 6' END) AS reasons
    FROM facts
)
SELECT 'FACT|expired_lessons|' || expired_lessons FROM verdict UNION ALL
SELECT 'FACT|voided_allowances|' || voided_allowances FROM verdict UNION ALL
SELECT 'FACT|grace_slots_used|' || grace_slots_used FROM verdict UNION ALL
SELECT 'FACT|first_speech_failures|' || first_speech_failures FROM verdict UNION ALL
SELECT 'FACT|max_schema_version|' || max_schema_version FROM verdict UNION ALL
SELECT 'FACT|ledger_rows|' || ledger_rows FROM verdict UNION ALL
SELECT 'FACT|ledger_rows_without_lesson_rows|' || ledger_rows_without_lesson_rows || ' (informational: dropped with the ledger, harmless)' FROM verdict UNION ALL
SELECT CASE WHEN reasons = '' THEN 'SAFE|no enforce-mode data exists; rolling back loses only 24-hour window state'
            ELSE 'UNSAFE|' || reasons END FROM verdict;
