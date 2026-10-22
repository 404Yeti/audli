-- READ-ONLY report for the observe-mode window. One line per metric: ALERT|INFO|OK, name, detail. :since is a timestamptz, :enforce is 1 in enforce mode (voids, grace slots and expired lessons are then normal and only informational).
WITH latest AS (SELECT DISTINCT ON (user_id) user_id, started_at FROM lesson_lifecycles ORDER BY user_id, started_at DESC, id DESC),
pairs AS (SELECT started_at, lag(started_at) OVER (PARTITION BY user_id ORDER BY started_at, id) AS previous FROM lesson_lifecycles)
SELECT * FROM (
  SELECT 1 AS n, 'INFO' AS verdict, 'lesson_starts_since' AS name, count(*)::text AS detail FROM lesson_lifecycles WHERE started_at >= :'since'
  UNION ALL SELECT 2, 'INFO', 'distinct_learners_started_since', count(DISTINCT user_id)::text FROM lesson_lifecycles WHERE started_at >= :'since'
  UNION ALL SELECT 3, 'INFO', 'ledger_rows_total_and_touched_since', (SELECT count(*) FROM lesson_entitlements) || ' / ' || (SELECT count(*) FROM lesson_entitlements WHERE updated_at >= :'since')
  UNION ALL SELECT 4, 'INFO', 'second_starts_inside_24h_since (would_refuse, from lesson rows)', count(*)::text FROM pairs WHERE started_at >= :'since' AND previous IS NOT NULL AND started_at < previous + interval '24 hours'
  UNION ALL SELECT 5, CASE WHEN count(*) = 0 THEN 'OK' ELSE 'ALERT' END, 'learner_with_lessons_but_no_ledger_row', count(*)::text FROM latest l WHERE NOT EXISTS (SELECT 1 FROM lesson_entitlements e WHERE e.user_id = l.user_id)
  UNION ALL SELECT 6, CASE WHEN count(*) = 0 THEN 'OK' ELSE 'ALERT' END, 'allowed_new_window_not_recorded_in_ledger', count(*)::text
    FROM lesson_lifecycles l JOIN lesson_entitlements e USING (user_id) WHERE l.started_at >= e.next_eligible_at AND l.started_at > e.last_started_at
  UNION ALL SELECT 7, CASE WHEN count(*) = 0 THEN 'OK' ELSE 'ALERT' END, 'window_not_exactly_24h_and_not_voided', count(*)::text FROM lesson_entitlements WHERE voided_at IS NULL AND next_eligible_at <> last_started_at + interval '24 hours'
  UNION ALL SELECT 8, CASE WHEN :enforce = 1 THEN 'INFO' WHEN coalesce(sum(grace_actions + first_speech_failures), 0) + count(voided_at) = 0 THEN 'OK' ELSE 'ALERT' END, 'enforce_only_fields_written (voids, grace, failures)', (count(voided_at) || ', ' || coalesce(sum(grace_actions), 0) || ', ' || coalesce(sum(first_speech_failures), 0)) FROM lesson_entitlements
  UNION ALL SELECT 9, CASE WHEN :enforce = 1 THEN 'INFO' WHEN count(*) = 0 THEN 'OK' ELSE 'ALERT' END, 'expired_lessons', count(*)::text FROM lesson_lifecycles WHERE status = 'expired'
  UNION ALL SELECT 10, 'INFO', 'ledger_ahead_of_latest_lesson (cleanup/restart can cause this legitimately)', count(*)::text FROM lesson_entitlements e JOIN latest l USING (user_id) WHERE e.last_started_at > l.started_at
  UNION ALL SELECT 11, CASE WHEN max(version) = 6 THEN 'OK' ELSE 'ALERT' END, 'schema_version', coalesce(max(version)::text, 'none') FROM schema_migrations
) m ORDER BY n;
