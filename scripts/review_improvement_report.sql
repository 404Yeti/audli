-- AUD-77 Review language-coaching monitoring. READ-ONLY. Selects counts only: no learner text, IDs or hashes.
-- Run against production only with the owner's approval, e.g.  psql "$URL" -f scripts/review_improvement_report.sql
-- (the table exists only after migration 007).

-- 1. Outcomes per UTC day and prompt version, with the rejection code and whether the automatic formatting repair was used.
SELECT date(claimed_at) AS day, prompt_version, status, COALESCE(rejection, '-') AS rejection, repaired, COUNT(*) AS attempts
FROM review_improvements
GROUP BY date(claimed_at), prompt_version, status, COALESCE(rejection, '-'), repaired
ORDER BY day DESC, prompt_version, status, rejection;

-- 2. Rates per prompt version. rejection_pct = rejected / (ready + rejected): the figure to watch after a prompt or validator change.
SELECT prompt_version, COUNT(*) AS attempts,
       SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) AS ready,
       SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
       SUM(CASE WHEN status IN ('failed', 'abandoned') THEN 1 ELSE 0 END) AS failed_or_abandoned,
       SUM(CASE WHEN repaired THEN 1 ELSE 0 END) AS repaired,
       ROUND(100.0 * SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END)
             / NULLIF(SUM(CASE WHEN status IN ('ready', 'rejected') THEN 1 ELSE 0 END), 0), 1) AS rejection_pct
FROM review_improvements
GROUP BY prompt_version
ORDER BY prompt_version;

-- 3. Learner retries: passages decided more than once under a prompt version, and how many ended in a suggestion.
SELECT prompt_version, COUNT(*) AS passages_retried, SUM(recovered) AS recovered
FROM (SELECT exercise_id, prompt_version, MAX(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) AS recovered
      FROM review_improvements
      WHERE status IN ('ready', 'rejected')
      GROUP BY exercise_id, prompt_version
      HAVING COUNT(*) > 1) retried
GROUP BY prompt_version
ORDER BY prompt_version;
