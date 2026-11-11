# Daily Listening Challenge (AUD-117): design, plan and runbook

Behind `AUDLI_LISTENING_DAILY_ENABLED` (off by default). Nothing here runs, migrates or spends money by merging. Autonomous publication has its own second flag, `AUDLI_LISTENING_DAILY_PUBLISH` (also off).

**Product:** Audli makes one genuinely new, shared listening lesson per calendar day (Asia/Ho_Chi_Minh, the same boundary for every learner). It is the main card on the learner home all day. At the next boundary it moves into Discover for good and the next one goes live. A lesson is never reused, replayed or substituted.

## 1. Architecture (smallest thing that works on the existing stack)

| Piece | Where | Notes |
|---|---|---|
| Scheduler | `python -m app.listening.daily run` (`app/listening/daily.py`) | Third sidecar beside the API, generator and worker (`sidecar.py daily`, `scripts/start.sh`). No new Render service, no cron. A loop wakes every minute; every step is idempotent, so "cron semantics" are just comparing the clock to the schedule. |
| Generation | existing `generation_jobs.create_generation` / `finalize_reservation`, built by the existing generator process | Same Spec, voices, measured-cue audio, four exercises, validation, object storage. No second exercise system. |
| Publication | existing `library.register -> approve -> publish` | Run by one **service account** that must be on `AUDLI_LISTENING_CURATOR_IDS`. Reviewer is recorded as `auto:daily-gates-v1`, bound to the same digest of script, exercises and every audio variant. |
| Gates | `app/listening/daily_gates.py` | Pure functions; see section 4. |
| Topics | `daily_taxonomy.py` (16 categories, 90+ reviewed subtopics) + `daily_planner.py` (pure rotation) | AI proposes topics only when the pool runs low (section 5). |
| History | table `listening_daily_challenges` (migration 015) | One row per date, kept forever. |
| Learner side | `GET /api/listening/daily`, `daily_read.py`, Daily card on `LearnerHome` | Read-only. Completions and streaks are derived (section 6). |
| Operator | `python -m app.listening.daily status / pause / resume / retry DATE / plan`, `GET /api/listening/ops/health` (`daily` block) | Section 8. |

## 2. Timeline (ICT)

* From 23:00 (`AUDLI_LISTENING_DAILY_GENERATE_HOUR`) the scheduler claims tomorrow's row, plans a topic, and submits one generation request (`daily:<date>:a<attempt>`).
* The generator builds it (private to the service account). The scheduler validates it (gates + one bounded semantic review). A pass approves it through the library workflow and leaves it `scheduled` (still private).
* At 00:00 the next tick publishes it in one transaction (`library.publish` removes the owner row and the day row becomes `active`). Missed ticks catch up on the next one.
* A challenge is hidden from the Discover list while its date is today or later (`daily_read.hidden_lesson_ids`) and listed after the boundary. No job runs for that: it is the clock.
* If today's lesson does not exist yet (first day, an outage, all attempts failed earlier), generation also runs for **today** and publishes as soon as it is valid. Until then learners see an honest "being prepared" card.

## 3. Idempotency (a unique date is not the safety net)

* **Lease**: each date row has `lease_owner/lease_until`; a second scheduler gets `busy` and does nothing.
* **Compare-and-set**: every state change is `UPDATE ... WHERE state = expected AND lease_owner = me AND lease_until >= now`. A scheduler that lost its lease (slow, restarted, clock jump) gets `LeaseLost` and writes nothing.
* **Deterministic request id** `daily:<date>:a<n>` + the existing `UNIQUE(user_id, client_request_id)`: a retry after any crash finds the same request and the same reservation (never a second one).
* **Database uniqueness across all days**: `lesson_id` UNIQUE, `fingerprint` (sha256 of the normalised script) UNIQUE, and a CHECK that a `scheduled`/`active` day carries both. The database refuses to let one lesson or script be two days' challenge.
* **Publish is one transaction**: lock the day row, `library.publish` (locks the entry, re-checks the digest and readiness, removes the owner), mark `active`. A failure rolls back all of it; the lesson stays private and the day `scheduled`.
* Each step resumes from the persisted state, so a crash at any line is safe.

State machine: `planned -> generating -> built -> scheduled -> active`; a failed or rejected attempt returns to `planned` (new topic) while attempts remain (`AUDLI_LISTENING_DAILY_MAX_ATTEMPTS`, default 2), else `failed`.

## 4. Quality gates (all must pass; any failure rejects the candidate permanently)

Deterministic, free, first: **content** (planned CEFR/format/accents, exactly the four exercises, >=6 sentences, supported level only), **answer consistency** (MC key is an option and grounded; the blank answer reproduces a script sentence; the sequencing key orders exactly the items and is not already in order; spoken key points are grounded), **audio readiness** (variant in the planned accent, checksum/size/duration, every sentence aligned and ordered, speaking rate plausible, optional bucket probe), **safety** (blunt blocklist + personal-data patterns over script and questions), **duplicates** (lesson id, script fingerprint, content hash, audio checksum, 4-word-run overlap, title, TF-IDF content-word cosine against every shared lesson and earlier challenge).

Then one **bounded semantic review** (`OpenAIDailyJudge`, strict structured output, `store: false`, retries off, capped output) given the candidate and its three nearest neighbours: safe, coherent/answerable, level, and "same scenario as neighbour X" (catches a rewrite in different words). It is reserved against its own budget first. **Fail closed**: if it cannot run (budget, outage, global AI kill switch) nothing is published; the validated lesson waits and the operator is alerted. Only shared lessons and earlier challenges are compared or sent to the model; a learner's private lesson never is.

A rejected candidate is recorded (`rejected[]`: attempt, lesson id, topic, reasons), stays a private draft forever, and its topic is never offered again.

## 5. Topics

`daily_taxonomy.py`: 16 categories (travel, food, health, home/family, shopping/money, education, technology/media, nature, sports, culture/arts, community/civic, work, transport/city, science, history/stories, hobbies), each with a scenario and the CEFR band it suits. **Only A1-B2.** `AUDLI_LISTENING_DAILY_LEVELS` refuses C1/C2 at startup until the pipeline is shown to validate them.

`daily_planner.py` rotation (deterministic, hash tie-breaks): a subtopic once ever; category differs from the last 3 days; work + food/café together at most 3 of the last 14 days; CEFR least-recently-used and never the same as yesterday; listening format and accent least-recently-used among **supported** combinations only (`format_availability`). A combination without an approved voice pair is never offered, so nothing is ever substituted.

**AI fallback:** when fewer than `AUDLI_LISTENING_DAILY_POOL_LOW` (14) reviewed topics are unused the operator is alerted and the scheduler asks the small model for new seeds on alternate days (every day once the pool is empty). A proposal is kept only if it is well-formed, in a known category, passes the safety patterns, and is not a rewording (content-word overlap) of any existing title, topic or pool scenario. If the proposal cannot be obtained the day falls back to the pool, or fails without spending.

## 6. Completions and streaks

Derived, not stored. A day is credited when the learner has a `completed` attempt on that day's lesson **whose completion falls on the challenge's own ICT date**. Attempts are immutable once completed (database trigger), so credit is idempotent by construction: refresh, retry, two tabs or a replay can never credit a day twice, no write happens in the learner request, existing sessions are untouched, and a deployment without migration 015 answers exactly as before. Finishing an old challenge later from Discover is a normal lesson play and earns no streak. Streak = consecutive credited days ending today (or yesterday while today is still open); longest streak and total days are reported.

Streaks are an explicit product requirement for this feature (AGENTS.md lists gamification as not yet approved; this is that approval, limited to the daily streak).

## 7. Spending and switches

* `AUDLI_LISTENING_DAILY_MAX_USD` (0.60) caps generation reservations per challenge date across all attempts; `AUDLI_LISTENING_DAILY_REVIEW_MAX_USD` (0.10) caps semantic review + topic proposals per date. Both sit under the existing global limits (`GEN_DAILY_BUDGET_USD`, monthly credits, `AI_DAILY_BUDGET_USD`). Estimates, not billing: keep the provider-side spend limits.
* The service account needs the curator allowance for one request per attempt: `AUDLI_LISTENING_GEN_CURATOR_DAILY=3` or more (ceiling 5). If it is missing the scheduler says so in the alert.
* `AUDLI_LISTENING_DAILY_PUBLISH=false` (default): generate and validate to `scheduled` only (a dry run an owner can inspect), never publish. It still spends on generation; to spend nothing, leave `AUDLI_LISTENING_DAILY_ENABLED` off.
* `python -m app.listening.daily pause` sets a DB switch that stops generation **and** publication at once, without a redeploy; `resume` continues. The global AI kill switch stops new generation and review but not publishing something already validated.

## 8. Operator visibility and alerts

`python -m app.listening.daily status` (and the `daily` block of `/api/listening/ops/health`): per-date state, attempts, topic, review spend, failures, last events, pool size, diversity counts. Alerts (once per date and code) go to the ERROR log and, if configured, `AUDLI_LISTENING_DAILY_ALERT_WEBHOOK` (a Slack-compatible URL): a rejected attempt, all attempts failed, per-date cap reached, generation refused, review down, authorization problem, pool low. Health also alerts when today's challenge is not live after 01:00 ICT, when it failed, or when paused. The scheduler itself also sends one `published (live)` notice when a challenge goes live and one deduplicated `not_live (<state>: <reason>)` alert from 01:00 ICT if today's challenge is not active (never while paused, never when publication is off); delivery is best effort and never blocks the scheduler (docs/LISTENING-DAILY-ACTIVATION.md, Notifications).

## 9. Security

* One new process, one new role: `docs/postgres-listening-daily-role.sql` (generated by `roles.daily_role_sql`). It has the library workflow's grants (including DELETE on lesson owners, which the API and generator lack) plus its own two tables, and **no learner data** (no attempts, answers, spoken answers, recordings, accounts). It gets the AI key but not the speech key, any bucket credential, the recordings credentials or the signing secret (`sidecar.DAILY_PASS_THROUGH`).
* Autonomous publication reuses the curator authorization unchanged: the service account must be on the operator curator list, the lesson must be owned and generated by it, approval is digest-bound, and the API role can only read the daily tables. A service account that loses curator status publishes nothing.
* Audit: reviewer `auto:daily-gates-v1`, the full `gate_report`, `rejected[]` and an `events` trail are stored on the day row.
* The learner response contains no answer key, explanation, rubric, storage key or checksum (tested).

## 10. Operator rollout (each step needs the owner; none happens by merging)

1. Review and apply migration 015: `python -m scripts.migrate_postgres --listening` (requires 009-012 and 014; additive, two tables; read `docs/postgres-015.sql`). Take the usual backup first.
2. Create the role `audli_listening_daily` (`CREATE ROLE ... LOGIN PASSWORD ...`), run `docs/postgres-listening-daily-role.sql`, and re-run `docs/postgres-listening-api-role.sql` (it now grants the API read-only access to the two tables).
3. Create (or pick) the service account user, add it to `AUDLI_LISTENING_CURATOR_IDS`, set `AUDLI_LISTENING_DAILY_SERVICE_ID`, `AUDLI_LISTENING_GEN_CURATOR_DAILY=3`, `AUDLI_LISTENING_DAILY_DATABASE_URL`, and `AUDLI_LISTENING_DAILY_ENABLED=true` with `AUDLI_LISTENING_DAILY_PUBLISH=false`. Deploy. Run `python -m app.listening.daily plan --days 7` to preview topics (no spend).
4. **Dry run (spends the first money):** with publish still false, let one evening run. Check `status`, listen to the lesson in the app as the service account, read `gate_report`.
5. Only then set `AUDLI_LISTENING_DAILY_PUBLISH=true`. To stop: `python -m app.listening.daily pause` (instant) or set the flag false.
6. Set the NEXT_PUBLIC flags as for 2.0; the card appears when `/api/listening/daily` exists.

Nothing in this change touches the five existing published lessons, Audli Classic, or the Supabase schema ledger.

## 11. Known limits

* The audio-readiness gate checks stored metadata (checksum, size, duration, alignment, speaking rate). The generator already refuses to mark a lesson `ready` unless storage was written and verified; a bucket probe can be passed to `Daily(audio_probe=...)` if the scheduler is ever given a read-only bucket credential, which it does not have today.
* Semantic similarity is lexical (TF-IDF over content words) plus the model's "same scenario" check. It cannot prove two lessons are unrelated; it is built to catch copies, light rewrites and the same scene retold.
* Generation uses the single curator-allowance request per attempt; a day with two attempts uses two of the service account's UTC-day requests.
* The reviewed pool has 90+ topics (about three months before the AI fallback starts proposing at 14 unused). It needs a human to add reviewed topics over time.
