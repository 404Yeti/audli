# Audli 2.0 post-launch verification (2026-11-05): AUD-103, AUD-104, OpenAI spend, R2 retention, worker health

Status words: **verified in production** (read-only inspection today), **verified locally** (automated tests), **open** (needs the owner or an account we do not have here).

## AUD-103 cross-account isolation: STILL OPEN
| Check | Status |
|---|---|
| Two-account HTTP tests (attempts, answers, skip, retry, recording, audio, progress, library, generation request status, generated-lesson open) on SQLite and PostgreSQL | verified locally: `tests/test_listening_journey.py`, `_generation_api.py`, `_library.py`, `_allowlist.py`, `test_listening.py` |
| No route accepts a learner identity from the client; every data route scopes by the verified identity | verified locally: new `tests/test_listening_isolation_guards.py` (static check of the route table) |
| Signed media bound to its key and expiry; recording keys can never be signed | verified locally: same file, plus `test_listening_storage_isolation.py` |
| Row-level security on all 17 `listening_*` tables; `anon`, `authenticated` and `service_role` hold no SELECT | **verified in production** (catalog query against the live project, no row data read) |
| Real two-account run against https://app.audli.ai (`~/audli-isolation-check.py`) | **OPEN.** It needs two confirmed accounts' access tokens, which this session does not have. Not claimed as passed. |

**Documented limitation (separate from ownership):** ownership is checked when a signed audio URL is *issued*, not when it is fetched. A URL already handed out works for whoever holds it until it expires (300 s; `SIGNED_URL_SECONDS`). Pinned by a test. Not a cross-account leak: a learner only ever receives URLs for their own attempts.

To close AUD-103: sign up a second confirmed account, export both access tokens in your shell, run `python3 ~/audli-isolation-check.py`, expect `ALL PASSED`, then attach the output (no tokens) to AUD-103.

## AUD-104 privacy policy: drafted, awaiting owner/legal approval
Page: `/privacy` (web), linked from the app shell, the generator form, the spoken-answer screen and the marketing site footer. Last updated 5 November 2026.

**Decisions only you can make (the policy says nothing beyond what is verified):**
1. **Contact address.** The page uses `robbie@audli.ai`, which already appears on the marketing site. Confirm it is the privacy contact.
2. **Deletion promise.** `AUDLI_ACCOUNT_DELETION_ENABLED` is off by default and its state in production was not visible to us. The page says self-service deletion is not available and to email. Also: 2.0 learner rows cascade from `users`, but a **generated lesson's content and its audio object in R2 are not owned by a `users` foreign key**, so they would remain after a user is deleted. Decide whether deletion on request includes a manual step for those, then say so. Code change is not in this PR.
3. **Recording expiry.** The page does not state a number of days: the R2 lifecycle rule could not be inspected (below).
4. **Provider retention.** OpenAI: no training by default, abuse-monitoring logs up to 30 days, audio transcription excepted (OpenAI docs). ElevenLabs: standard retention; zero-retention is enterprise-only and not enabled. Confirm you accept this, or buy/enable ZDR.
5. **Backups.** Supabase backup expiry is unverified; the page says so.
6. Terms, cookies and "we do not sell data" statements are legal/business claims and are deliberately absent.
7. Figma Settings shows "Voice recordings: 30 days". That is **not** how Audli works (recordings are deleted on transcription) and is not implemented. See the Settings PR.

## OpenAI spending controls
In-app (verified locally and in production data): the $2/day assessment guard (`listening_ai_limits`: today reserved about $0.001, not halted); generator ledger: day $0.577 of the $5 ceiling, month 2,319 of 100,000 credits. Reconciliation: month credits 2,319 = 1,239 (4 ready lessons) + 1,080 (2 failed lessons, charged because synthesis was really billed). The day USD figure exceeds the sum of the four ready requests by exactly the failed lessons' share, i.e. failed jobs are charged only for what the provider billed. No reservations are stuck: no request is in a working phase, no job is leased.
Limits are unchanged. Provider errors do not retry uncontrollably (SDK retries off; one controlled correction pass for scripts).
**Provider-side budget: cannot be inspected from here** (no OpenAI admin access). Steps for you: platform.openai.com → Settings → Organization → Limits (or Project → Limits) → set the project's **monthly budget** and, if offered, a **project spend limit**; add an email alert at 50/80%. A *budget with alert* only notifies; a *project limit* is the hard cap. Verify which your account offers; do not rely on an alert as a cap. Recommended: hard limit at about 2× expected month spend (the app's own ceilings are about $150/month at $5/day).

## Cloudflare R2 recording retention: OPEN (owner action)
No R2 credentials or connector exist in this environment, so the bucket was not inspected. Code behaviour verified locally: recordings live under `listening-tmp/` in their own store (separate bucket/credentials in production), are never signable, are deleted after transcription or failure, and the worker sweep removes leftovers.
To verify/configure (Cloudflare dashboard → R2 → the **recordings** bucket → Settings → Object lifecycle rules): add a rule *Delete objects* **1 day after upload**, prefix `listening-tmp/` (or the whole bucket if it only holds recordings). Do **not** add it to the lessons bucket. Confirm the bucket has no public access (Settings → Public access: R2.dev subdomain disabled, no custom domain). Then tell us and the policy can state the backstop.

## Worker and production health (Render, read-only)
Live deploy = commit e907d9b (PR #134), healthy. API+spoken worker+generator run as sidecars in one 512 MB container (`scripts/start.sh`): memory steady at about 321 MB (60%), CPU under 1%. Only error logs in the window are startup configuration errors from 06:10-06:21 UTC during the rollout (S3 settings), resolved by later deploys. Jobs: 4 generate and 7 interpret succeeded, 2 generate failed (13:03, before the memory/stall fixes), 2 spoken succeeded; none stuck. Generator sequential and spoken/generator isolation: unchanged code, covered by `tests/test_listening_system.py` and `test_listening_generation.py`. Remaining risk: one container, so a memory spike in the generator affects the API (observed once, fixed by #134).
