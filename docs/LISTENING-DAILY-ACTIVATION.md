# Daily Challenge: production activation runbook (AUD-117)

**Nothing in this document has been executed.** Every step that touches production (migration, database role, environment, paid generation, publication) needs your explicit approval first; each stage below ends at an approval gate. Merging the code changes nothing in production: the feature, the scheduler and publication are all off by default.

Sequence: **A** make the schema and the (empty) card available, **B** one controlled real challenge in dry-run (the first and only paid step), **C** review it and publish it, **D** let the schedule run. Automatic generation and publication stay disabled until you approve C.

## 0. Preconditions
* PR #163 merged, CI green, production deployed and healthy (`/api/health` 200). The five published lessons and `/classic` unchanged.
* `SELECT version FROM listening_migrations ORDER BY 1;` returns exactly `9,10,11,12,14` (013 absent unless separately approved).
* The curated-library workflow works for you today (migration 014 is live) and the generator process is already running in production (`AUDLI_LISTENING_GENERATION_ENABLED=true`, generator role, speech key). The daily scheduler only queues requests; the generator builds the audio.
* Provider-side spend limits are set at OpenAI and ElevenLabs (the in-app ceilings are estimates, not billing).

## Stage A: schema, role, and an inert card (no spend)

### A1. Backup (recovery point)
Confirm the Supabase backup / PITR window covers now and note the timestamp. Also:
`pg_dump "$OWNER_URL" --no-owner -t 'listening_*' -f listening-pre-015-$(date -u +%Y%m%dT%H%M%SZ).sql` (private; contains learner data; delete after the verification window).

### A2. Apply migration 015 (needs your approval)
Verify the files, then apply **only** 015 as the database owner, in one session. Do **not** use `python -m scripts.migrate_postgres --listening` (it applies every pending listening migration).
```
sha256sum docs/postgres-015.sql docs/postgres-listening-daily-role.sql docs/postgres-listening-api-role.sql
#  89aa33c875eddb8f2a70301de40dd4ee67cf771d7b19d5a0133ee7ef0cbb1fb1  docs/postgres-015.sql
#  980c821d9059cbe402e4968275b12b8ef07e5f9d93615809b7dced4ca039fe79  docs/postgres-listening-daily-role.sql
#  58d409528d20fd0d80f5e67a2f6e47a4587501535ddeea884f84fc981ae8d13b  docs/postgres-listening-api-role.sql
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f docs/postgres-015.sql
```
(If the hashes differ from the merged files, stop: the numbers above were taken from the PR head before merge.) The script is one transaction, fails fast (`lock_timeout 5s`), refuses a second run, creates two new tables (`listening_daily_challenges`, `listening_daily_control`), enables row-level security, strips Supabase client roles, and asserts the resulting privileges or rolls everything back.

### A3. Create the daily role and re-run the API role SQL (needs your approval)
```
psql "$OWNER_URL" -c "CREATE ROLE audli_listening_daily LOGIN PASSWORD '<new strong password, not reused anywhere>'"
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f docs/postgres-listening-daily-role.sql
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f docs/postgres-listening-api-role.sql      # idempotent; adds read-only access to the two new tables
```
Use the Session Pooler URL for the new role the same way the generator's URL is formed.

### A4. Verify (read-only)
```sql
SELECT version FROM listening_migrations ORDER BY 1;                                                    -- 9,10,11,12,14,15
SELECT relname, relrowsecurity FROM pg_class WHERE relname LIKE 'listening_daily_%' AND relkind='r';    -- both t
SELECT count(*) FROM listening_daily_challenges;                                                        -- 0
SELECT has_table_privilege('audli_api_runtime','listening_daily_challenges','SELECT'),                  -- t
       has_table_privilege('audli_api_runtime','listening_daily_challenges','INSERT'),                  -- f
       has_table_privilege('anon','listening_daily_challenges','SELECT'),                               -- f
       has_table_privilege('authenticated','listening_daily_challenges','SELECT');                      -- f
SELECT has_table_privilege('audli_listening_daily','listening_lesson_owners','DELETE'),                 -- t (publish)
       has_table_privilege('audli_listening_daily','listening_daily_challenges','DELETE'),              -- f (history is permanent)
       has_table_privilege('audli_listening_daily','listening_attempts','SELECT'),                      -- f (no learner data)
       has_table_privilege('audli_listening_daily','users','SELECT');                                   -- f
SELECT count(*) FROM listening_lesson_owners;                                                           -- unchanged from before
```

### A5. Make the (empty) card appear (optional; no spend, nothing generated)
On the Render API service set `AUDLI_LISTENING_DAILY_ENABLED=true` and **leave `AUDLI_LISTENING_DAILY_DATABASE_URL` unset**. `scripts/start.sh` then prints "no challenge will be generated or published" and starts no scheduler. Learners see the calm "Today's challenge is being prepared" card; nothing is hidden from Discover (there are no challenges). Check `/api/listening/daily` returns `status: unavailable`, the home screen still loads, and the five lessons are unchanged. No change is needed on Vercel (the card appears when the route exists).

**Gate A: you approve moving to Stage B.**

## Stage B: one controlled real challenge, dry-run (the first paid step)

Needs your explicit approval of the spend. Expected cost: one lesson of about two minutes (one generation attempt: script model call + speech credits + one small review call). Hard caps for this date are `AUDLI_LISTENING_DAILY_MAX_USD` (default 0.60, generation) and `AUDLI_LISTENING_DAILY_REVIEW_MAX_USD` (default 0.10, review), under the existing global limits.

### B1. Service account
Create or choose one account that exists only to own daily drafts (not a person's learning account). Add its user id to `AUDLI_LISTENING_CURATOR_IDS` (on every process that reads it: the daily scheduler; the library CLI) and set `AUDLI_LISTENING_DAILY_SERVICE_ID` to it.

### B2. Environment on the Render API service (which also launches the sidecars)
| Variable | Value | Why |
|---|---|---|
| `AUDLI_LISTENING_DAILY_ENABLED` | `true` | |
| `AUDLI_LISTENING_DAILY_PUBLISH` | `false` | **dry-run: generate and validate, never publish** |
| `AUDLI_LISTENING_DAILY_DATABASE_URL` | the `audli_listening_daily` URL | starts the scheduler |
| `AUDLI_LISTENING_DAILY_SERVICE_ID` | the service account id | |
| `AUDLI_LISTENING_CURATOR_IDS` | includes the service account | existing curator authorization |
| `AUDLI_LISTENING_GEN_CURATOR_DAILY` | `3` | lets the service account queue one request per attempt (ceiling 5) |
| `AUDLI_LISTENING_DAILY_MAX_ATTEMPTS` | `1` | **a failed dry-run costs at most one lesson** |
| `AUDLI_LISTENING_DAILY_MAX_USD` / `_REVIEW_MAX_USD` | `0.60` / `0.10` (defaults) | per-date caps |
| `OPENAI_API_KEY` | already set | review model call |
Already required and unchanged: `AUDLI_LISTENING_ENABLED`, `AUDLI_LISTENING_GENERATION_ENABLED`, `AUDLI_LISTENING_GENERATOR_DATABASE_URL`, the generator's speech key and lesson-bucket credentials, `AUDLI_LISTENING_FORMATS_ENABLED` / `AUDLI_LISTENING_FORMAT_ACCENTS` (the daily process reads the same two switches as the generator, so it only plans accents that are really open). The daily process is never given the speech key or any bucket credential.

### B3. Preview before any spend
`python -m app.listening.daily plan --days 7` (operator shell, no spend, no writes) shows the next seven planned topics, levels, formats and accents. Check they look right and use only open accents.

### B4. Deploy and watch
On deploy the scheduler creates today's row immediately (it generates today's challenge when none exists), queues **one** request, the generator builds it, and the scheduler validates it to `scheduled`. It does **not** publish (flag off). It will create tomorrow's row only after 23:00 ICT, so:
```
python -m app.listening.daily status            # state per date, spend, gates, last events
python -m app.listening.daily pause --note "controlled challenge under review"     # immediately after today's row is `scheduled`
```
Pausing stops generation and publication at once, so exactly one challenge is made. (If the row ends `failed` or the lesson is rejected, `status` shows why and nothing was published; the cost is bounded by the caps.)

**Gate B: review the result.**

## Stage C: review, then publish the controlled challenge

1. As the service account in the app (the lesson is still private to it), play the lesson end to end: audio, transcript, all four exercises, debrief. Read `status`: the gate report (`content`, `answers`, `audio`, `safety`, `duplicates`, `semantic`) must all be ok and `nearest` similarities low.
2. Record your approval, then enable publication: set `AUDLI_LISTENING_DAILY_PUBLISH=true`, `AUDLI_LISTENING_DAILY_MAX_ATTEMPTS=2`, redeploy, and `python -m app.listening.daily resume`. The next tick publishes today's validated row in one transaction (the lesson becomes shared, the day `active`). It does not appear in Discover until the day is over.
3. Verify as an ordinary learner account: the home card shows the title, level, topic, accent and Start; finishing it shows Completed today and a 1-day streak; the lesson is **not** in the Discover list today.
4. If anything is wrong: `python -m app.listening.daily pause` (instant), then `python -m app.listening.library unpublish <lesson_id>` to make it private again.

**Gate C: you approve letting the schedule run.**

## Stage D: normal operation
From 23:00 ICT the scheduler prepares tomorrow's challenge, validates it, and publishes it at 00:00 ICT. Watch for the first full cycle: `status` (state `scheduled` before midnight, `active` after), the ops-health `daily` block (`/api/listening/ops/health`), alerts in the Render log (search `DAILY CHALLENGE ALERT`) and, if you set `AUDLI_LISTENING_DAILY_ALERT_WEBHOOK`, in your channel. Add reviewed topics to `app/listening/daily_taxonomy.py` before fewer than 14 remain unused (it alerts).

## Notifications (optional, `AUDLI_LISTENING_DAILY_ALERT_WEBHOOK`)
Nothing is sent unless this variable is set; the log (`DAILY CHALLENGE ALERT`, ERROR) and the ops-health `daily` block always report. No new service or credential is involved: the existing daily scheduler posts the message itself.

**Format.** One HTTPS `POST` with `Content-Type: application/json` and the body `{"text": "<message>"}` (a Slack incoming-webhook payload), 5 second timeout, no retries. Set the variable to the webhook URL:
* Slack: `https://hooks.slack.com/services/T…/B…/…` (a Slack "Incoming Webhook"; success is HTTP 200 with body `ok`).
* Discord: the channel's webhook URL with `/slack` appended: `https://discord.com/api/webhooks/<id>/<token>/slack`.
* Mattermost / Rocket.Chat incoming webhooks accept the same `{"text": ...}` body.
The URL is a secret (`SecretStr`): it is passed only to the daily scheduler process, never logged and never part of a message. Set it on the Render API service; the scheduler restarts with the redeploy.

**Messages** (`Audli daily challenge <date>: <event> (<status>[: <reason>])`; a date, an event, a status and a fixed safe reason; no lesson, topic, learner or secret):
* `published (live)`: once, when the day's challenge goes live. No message by about 00:05 ICT means look.
* `not_live (<state>: <reason>)`: once per date, from 01:00 ICT, if today's challenge is not active (`failed: every attempt was rejected or failed`, `scheduled: validated but not published`, `planned|generating|built: still being prepared`).
* the existing alerts: a rejected attempt, all attempts failed, a cap reached, generation refused, review down, authorization problem, low topic pool.

**What stays quiet.** `not_live` is never sent while the scheduler is paused (a paused scheduler does nothing, so nothing is checked) or while `AUDLI_LISTENING_DAILY_PUBLISH` is false (nothing is expected to go live).

**What it cannot do.** Delivery is best effort: an unreachable webhook is logged as a warning and never blocks generation or publication. A dead scheduler cannot send anything, so a missing `published` message is itself the signal.

## Rollback and recovery
* **Stop everything now:** `python -m app.listening.daily pause` (generation and publication stop; a challenge that is already live stays visible to learners, because the pause switch only governs the scheduler. To withdraw a live challenge use `python -m app.listening.library unpublish <lesson_id>`: learners then see the unavailable card). Or set `AUDLI_LISTENING_DAILY_PUBLISH=false` / `AUDLI_LISTENING_DAILY_ENABLED=false` and redeploy.
* **Withdraw a published challenge:** `python -m app.listening.library unpublish <lesson_id>` (it becomes private to the service account; learners' past attempts are kept).
* **A failed day:** it stays `failed` and learners see "being prepared"; never an old lesson. After fixing the cause, `python -m app.listening.daily retry <YYYY-MM-DD> --extra 1`.
* **Remove the schema** (only if no challenge has been published, or after exporting `SELECT * FROM listening_daily_challenges` privately): `BEGIN; DROP TABLE listening_daily_challenges; DROP TABLE listening_daily_control; DELETE FROM listening_migrations WHERE version = 15; COMMIT;` The API starts without the tables (it only reads them defensively), so the code can stay deployed.
* **Remove the role:** `DROP OWNED BY audli_listening_daily; DROP ROLE audli_listening_daily;`

## Approval checklist (nothing proceeds without each)
- [ ] A2: apply migration 015 to production (after the A1 backup)
- [ ] A3: create `audli_listening_daily` and run the two role SQL files
- [ ] A5: set `AUDLI_LISTENING_DAILY_ENABLED=true` on the API (optional, inert)
- [ ] B1/B2: create/choose the service account, set the Stage B environment (publish **off**, attempts **1**)
- [ ] B4: authorize the one paid dry-run challenge (cap about $0.70 including review)
- [ ] C2: authorize publishing the reviewed challenge and raising attempts to 2
- [ ] D: authorize leaving the schedule running
