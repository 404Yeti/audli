# AUD-47 observe-mode runbook (72-hour window)

Observe mode changes nothing a learner can see: it writes the daily-lesson ledger when a lesson starts and logs `lesson_limit would_refuse` where enforcement
*would* have refused. It never blocks, never takes grace slots, never returns 429, and Home keeps its current (legacy) view because the frontend only changes when
`enforced` is true. Migration 006 (schema 6) is already applied. **Enforce is out of scope for this runbook.**

## 1. Activation (production-config change; needs the owner's explicit approval)
Service `audli-api` (`srv-db270erbc2fs73fg85gg`, workspace `tea-dauqlhd9fdbs739vmkv0`). Only this one variable changes; everything else (ElevenLabs, AUD-73, accents, budgets) is untouched.

Dashboard: Render → `audli-api` → Environment → set `AUDLI_LESSON_LIMIT_MODE` = `observe` → Save (Render starts one new deploy of the same commit).
Equivalent MCP call (merge, not replace, so no other variable is touched):
`update_environment_variables(serviceId="srv-db270erbc2fs73fg85gg", workspaceId="tea-dauqlhd9fdbs739vmkv0", envVars=[{key:"AUDLI_LESSON_LIMIT_MODE", value:"observe"}], replace=false)`

Before: no other deploy in progress, `/api/health` 200, `05-postcheck.sh --target production` PASS, no merge to `main` planned during the window (any merge redeploys).
Immediately after (T+0, record the UTC time as `ACTIVATED`):
1. Render deploy for the same commit becomes `live`; startup log contains `Application startup complete` and **no** `requires migration 006`.
2. `/api/health` returns 200 with the same `revision`.
3. A Render event shows the environment-variable update (the health payload does not expose the mode; the first lesson start in the window proves it, see metric 3 below).
Expect up to about a minute of unavailability while the single free-plan instance restarts.

## 2. Rollback (no database change, ever)
Set `AUDLI_LESSON_LIMIT_MODE` back to `off` (dashboard, or the same call with `value:"off"`; deleting the variable is equivalent because `off` is the default).
The application stops reading and writing the ledger at once; the ledger rows and schema 6 stay in place and are harmless (a ledger-unaware reading uses each learner's latest lesson start, which is tested).
Do **not** run the SQL rollback for an observe problem: it is for schema-level trouble only, needs its own approval and a live SAFE verdict, and is not needed to return to `off`.

## 3. Monitoring (T+0, +15 min, +1 h, +6 h, +24 h, +48 h, +72 h; then on every learner report)
**Database half (read-only):** `scripts/migration006/08-observe-report.sh --since <ACTIVATED>` (exit status 1 on any ALERT). Metrics: lesson starts and distinct learners since activation,
ledger rows and rows touched, second starts inside 24 h (the DB-derived would-refuse count), learners with lessons but no ledger row, allowed new windows missing from the ledger,
windows that are not exactly 24 h, enforce-only fields written (voids, grace slots, first-speech failures), expired lessons, schema version. Rows touched start at 7 because the backfill set `updated_at`; only increases after `ACTIVATED` mean ledger writes.
`05-postcheck.sh --target production --phase observe` is the stricter schema check.

**Log half (Render `list_logs`, service `audli-api`, since `ACTIVATED`):**
| Look for | Meaning | Expected |
|---|---|---|
| level `error`, or text `Traceback`, `RuntimeError`, `lesson_cooldown_active`, `lesson_window_invalid`, `lesson_window_extension_refused` | application or ledger-guard error | none |
| request lines with ` 429 ` | an API refusal; observe must never refuse | **none** |
| request lines with ` 500 `, ` 502 `, ` 503 ` | server errors, especially `/api/lessons/start`, `/api/eligibility`, `/api/lessons/*` | none |
| `lesson_limit would_refuse code=lesson_cooldown where=lesson.start` | a learner started a second lesson inside 24 h (observe let it through) | allowed; counted. Each should have a matching second start in the DB metric |
| `GET /api/eligibility` statuses | Home's revalidation | 200 (401 only for signed-out visitors) |
| `Application startup complete` after each restart, no `requires migration 006` | boots fine on schema 6 | present |

## 4. Stop conditions (return the flag to `off`, then investigate; no database change)
1. **Any 429** from an API route.
2. Any 5xx on lesson start, eligibility or lesson endpoints, or a crash loop / failed startup (including `requires migration 006`).
3. Any ledger-guard exception text (`lesson_cooldown_active`, `lesson_window_invalid`, `lesson_window_extension_refused`) or any other error from the limit code.
4. `08-observe-report.sh` ALERT on: no ledger row for a learner with lessons, an allowed new window not recorded, a window that is not 24 h, any enforce-only field written, any expired lesson, or a schema version other than 6.
5. The DB-derived would-refuse count and the `would_refuse` log lines disagree for the same period (investigate before trusting either).
6. A learner reports being unable to start or being locked out, or a lesson start that is visibly slower than before activation.
7. `/api/health` not 200 for more than 5 minutes.

Expected and **not** a stop condition: `would_refuse` log lines. Historical data shows 11 of the 24 lessons were started inside 24 h of the same learner's previous start (mostly testing), so enforcement will visibly refuse some current testers; observe lets them through.

## 5. Minimum evidence before anyone proposes enforcement
All of the following, and then a separate owner approval with its own plan:
1. At least 72 h in observe with no stop condition triggered, and `08-observe-report.sh` OK at the 24, 48 and 72 h checks.
2. At least 10 real lesson starts from at least 5 distinct real learners during observe (the beta has 9 accounts, so this may take longer than 72 h; the owner may lower it).
3. At least one natural or owner-supervised case of each: a second start inside 24 h (matching `would_refuse` log and DB row) and a start after the 24 h window with no `would_refuse` and an updated ledger row.
4. The owner has seen Home on a real iPhone in observe (unchanged legacy view) and, with a test account on a staging-style rehearsal, the enforce countdown and refusal screens.
5. Testers' expected impact is accepted (see the historical 11/24 above) and a message plan exists.

## 6. Enforce mode (activated 2026-10-22T02:00:54Z, deploy `dep-dbcmslavcj2c73f6mbbg`, by the owner's explicit authorization)
Rollback is the same single variable: set `AUDLI_LESSON_LIMIT_MODE` back to `off` (never SQL rollback or restore; preserve the database and logs for investigation).
Report with `08-observe-report.sh --mode enforce --since <ENFORCED>` (voids, grace slots and expired lessons become informational; ledger-integrity metrics 5, 6, 7 and the schema check still ALERT).
In enforce mode 429 `lesson_cooldown` / `lesson_time_expired` on lesson endpoints are the **expected** refusals; review each one. Return to `off` immediately if: a learner whose window had already passed (ledger `next_eligible_at` earlier than the refusal) or who had no lesson in the previous 24 h is blocked,
lesson starts fail (5xx), any ledger-integrity metric ALERTs or a ledger-guard exception appears, review/history/cached-audio replay is refused or errors, or the application is unhealthy (health not 200 for more than 5 minutes).
