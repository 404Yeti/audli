# Audli 2.0 production cutover: what is built, what is proven, and the exact requirements

Audli 2.0 is additive: it adds tables, routes and pages beside 1.x and changes no 1.x table, route or data. Existing accounts keep working because 2.0 attaches to the same
`users` rows (learner data cascades from them) and nothing 1.x owns is altered. Replacing the landing experience is a **switch**, not a migration, so it is reversible in minutes.

## The switch (all off in production today)
| Layer | Setting | Default | Effect |
|---|---|---|---|
| Backend | `AUDLI_LISTENING_ENABLED=true` | off | registers `/api/listening/*`; startup checks migrations 009-011, storage, provider |
| Web | `NEXT_PUBLIC_AUDLI_LISTENING=true` | unset | `/listening` exists (404 otherwise) |
| Web | `NEXT_PUBLIC_AUDLI_LISTENING_HOME=true` | unset | **cutover:** `/` redirects to `/listening`; the classic 1.x home stays reachable at `/classic` |
| Backend | `AUDLI_LISTENING_AI_ENABLED`, `python -m app.listening.ai_budget halt` | on | kill switch for all AI work |
| Backend | `AUDLI_LISTENING_OPS_TOKEN` | unset | enables the monitor endpoint `GET /api/listening/ops/health` |
Verified on real production builds: with everything unset `/` serves 1.x and `/listening` and `/classic` answer 404; with the pages flag only, `/listening` is served and `/` is unchanged;
with both, `/` redirects (307) to `/listening` and `/classic` serves the 1.x home (results in the pull request). The redirect is never produced unless **both** flags are on, so it cannot send anyone to a 404.
**Decision for you:** while cut over, 1.x-only features (homework, review, accents) are not linked from the new landing; they remain reachable at `/classic` until you retire 1.x.

## Built and proven locally (no hosted system touched)
* Every learner journey: library, saved sessions, four exercises, background-assessed spoken answer, debrief, progress; account isolation; recovery after refresh.
* The whole system as separate real processes on PostgreSQL 16: the real migrator, the seeding CLI, a real API, a real worker connected as the **restricted database role**, a full journey, signed playback, the kill switch across processes (`tests/test_listening_system.py`).
* Security: recordings in their own store with their own credentials; signing refuses recording keys; restricted worker role (tested, 19 refused statements); spending ceiling and kill switch; provider disclosure; migration 009 frozen by checksum.
* Natural-audio pipeline: per-sentence synthesis, measured cues, quality gates, new-version publishing, production refusal of development audio (mocked provider; **no real voice generated**).
* Live OpenAI validation on 2026-11-04 (about US$0.20): models, strict structured output, grounding, failure handling.
* Monitoring: `python -m app.listening.health` (exit 1 on any alert) and the token-protected endpoint; the worker logs outcomes only.
* **Rollback:** `docs/postgres-listening-rollback.sql` removes migrations 009-011 completely and refuses to run unless the session first says so in words (and a second statement if 2.0 attempts exist); tested on PostgreSQL: 1.x data and ledger are byte-for-byte unchanged, 1.x starts and knows the learner, and 2.0 can be applied again afterwards.
* Data preservation: migrations are additive and tested to preserve every existing 1.x row (including after 009, 010 and 011).

## Not proven (cannot be proven from here)
1. **A physical iPhone** running the journey in Safari (MediaRecorder / AAC, silent switch, interruptions, mobile data).
2. **Real natural audio**: ElevenLabs output, voices, cost and acceptance of the request. The committed lesson audio is the development voice and production refuses it.
3. **Real S3-compatible storage** with two buckets: presign, Range, expiry, lifecycle rule (mocked transport only).
4. **A managed PostgreSQL** (Supabase/Render): migrations 009-011, the worker role SQL, and the worker connecting through the provider's pooler.
5. **The hosted backend and worker**: Render service settings and environment were not inspected (needs a workspace choice by you); no worker service exists.
6. **Authentication through Supabase** end to end for the 2.0 routes (tested with the mocked auth server and the real middleware).
7. **Load**: concurrent learners, provider rate limits, and OpenAI's real latency and invoice.
8. **CI on the whole stack** after it lands on `main` (#123-#127 have not all run).

## Exact requirements for cutover (in order; each needs your explicit approval)
1. Merge the PR stack to `main` in order (#123, #124, then #125, #127, this one; #126 is independent). Production behaviour does not change: every switch above is off.
2. **Staging first** (docs/LISTENING-STAGING-PLAN.md): separate Postgres, API, worker, two buckets, web project, OpenAI key with a provider-side limit, ElevenLabs approval. Run migrations 009-011, the worker role, lesson seeding, and every "not proven" item above on staging, including the iPhone checklist (docs/LISTENING-M04-PLAN.md section 4).
3. Generate the natural lesson audio on staging after you approve the voices and a character budget (`scripts/build_listening_audio.py --approved-chars N`), listen to every lesson end to end, publish as a new version (`--revision 2`). Re-run the device checklist.
4. Production database: take a backup; rehearse 009-011 and the rollback on a copy; apply `python -m scripts.migrate_postgres --listening` as the owner; create the worker role; keep the owner credentials out of the worker.
5. Production backend: private storage (lessons and recordings buckets, 1-day lifecycle on recordings, versioning off), `AUDLI_LISTENING_*` settings, OpenAI key with its own spend limit, `AUDLI_LISTENING_AI_DAILY_BUDGET_USD`, `AUDLI_LISTENING_OPS_TOKEN`; deploy the worker as a separate service; seed the production lessons (natural audio only).
6. Monitoring before traffic: schedule `python -m app.listening.health` (or poll the endpoint) and page on any alert; practise `halt` and `resume`.
7. Turn on `AUDLI_LISTENING_ENABLED` and `NEXT_PUBLIC_AUDLI_LISTENING` for a **small allow-listed group first** (a separate staging-style web deployment or a short window), watch health and cost for a day.
8. Cutover: set `NEXT_PUBLIC_AUDLI_LISTENING_HOME=true` on the production web project and redeploy. **Rollback in minutes:** unset it and redeploy (learners land on 1.x again, nothing deleted). To also stop 2.0 server-side: unset `AUDLI_LISTENING_ENABLED` or `halt`. Only if the 2.0 data must be erased: the rollback SQL, after reading it.
9. Privacy: update the policy for the external provider, the transcript retention (kept until account deletion) and the recording deletion; confirm OpenAI's API retention terms.

## Rollback drill (run on staging before production)
`unset` the home flag and redeploy (expect `/` to serve 1.x); `halt` (expect uploads to answer 503 with the paused message); `resume`; stop the worker (expect the health check to alert within five minutes and answers to stay "being checked", recordings removed by the bucket rule within a day); run the rollback SQL on a copy of staging and re-apply the migrations.
