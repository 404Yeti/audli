# Audli 2.0 staging plan and invited-beta security review (AUD-100)

Status: **plan only; two of the code gaps found by this review are now implemented** (see section 3: the two-store split and the restricted worker role, on branch `feat/audli-2-beta-hardening`). Nothing has been provisioned, applied or paid for. Production stays untouched: `AUDLI_LISTENING_ENABLED` is off, `NEXT_PUBLIC_AUDLI_LISTENING` is unset
(so `/listening` answers 404 on the production web app), migrations 009-011 are not applied, no worker runs. Prices and provider features below are from memory or public
pages and are **unverified**; confirm each before approving spend.

## 1. Target shape
```
iPhone Safari ──https──▶ Vercel project "audli-staging"  (own project, own env, NEXT_PUBLIC_AUDLI_LISTENING=true, BACKEND_URL=staging API)
                                     │ /api/* rewrite
                                     ▼
                      staging API  (Render web service, AUDLI_ENVIRONMENT=production-like, AUDLI_LISTENING_ENABLED=true)
                         │   │ presigned GET (lessons bucket, read-only token)        ▲
                         │   └─ PUT recording (recordings bucket) ─────────────┐       │ browser <audio> fetches from the bucket
                         ▼                                                      ▼       │
              staging PostgreSQL (own database, migrations 001-007 [+008] then 009-011)   bucket "audli-stg-recordings"  bucket "audli-stg-lessons"
                         ▲                                                      ▲
                         └──── staging worker (separate process/service) ────────┘   (OpenAI via the spending guard, dedicated key with its own limit)
```
Nothing is shared with production: not the database, not the buckets, not the Supabase project, not the OpenAI key, not a Vercel project/environment.

## 2. Exact prerequisites (each needs your approval before it exists)
| # | Item | Recommended choice | Why | Cost note (unverified) |
|---|---|---|---|---|
| 1 | **Staging Postgres** | a new Supabase project (or any managed Postgres 15+), used only for staging | production-like (1.x uses Supabase); migrations are written for it | free tier likely enough |
| 2 | **Staging auth** | the same new Supabase project's Auth, 2-5 invited accounts | production forces server-verified Supabase identity; local mode must not be used for a public URL | free |
| 3 | **Staging API** | a second Render web service from this repo's Dockerfile, env below | same runtime as production | a small paid instance (~US$7/mo) or a free one that sleeps |
| 4 | **Staging worker** | a Render *background worker* from the same image: `python -m app.listening.worker` | the spoken answer needs a separate process; a free tier does not exist for workers | ~US$7/mo |
| 5 | **Object storage: two buckets** | Cloudflare R2 (or any S3-compatible, path-style, https origin): `audli-stg-lessons` and `audli-stg-recordings` | see section 3; R2 tokens are bucket-scoped, no versioning (so deleted recordings are really gone), lifecycle rules, no egress fee | free tier / cents |
| 6 | **Bucket lifecycle** | recordings bucket: expire every object after **1 day**; lessons bucket: none | backstop for a stopped worker or a deleted account | free |
| 7 | **Staging web** | a new Vercel project `audli-staging` from the same repo, production branch `staging` (or a fixed branch), SSO protection on | previews of the existing project share the production `BACKEND_URL`; a separate project cannot | Vercel plan permitting |
| 8 | **OpenAI** | a new project key with its own US$5 monthly limit and an alert | the in-app guard is an estimate; this is the real ceiling | pay as used, expected cents |
| 9 | **Real lesson audio** | per-sentence ElevenLabs synthesis, owner-approved voices, published as a new lesson version | the espeak audio is a development voice | a few dollars of credits |
| 10 | **Test devices** | one physical iPhone (current iOS, Safari), on Wi-Fi and mobile data | the beta goal | none |

New variables: `AUDLI_LISTENING_RECORDINGS_BUCKET`, `AUDLI_LISTENING_RECORDINGS_ACCESS_KEY`, `AUDLI_LISTENING_RECORDINGS_SECRET_KEY` and optionally `AUDLI_LISTENING_RECORDINGS_ENDPOINT` (required with S3 storage; startup refuses the same bucket or the same access key as the lesson audio). The API needs both credentials (read lessons, write recordings); the worker needs the recordings credential and a database URL for the worker role.

**Staging API environment** (names only; values are secrets or staging URLs): `AUDLI_ENVIRONMENT=production`, `AUDLI_PERSISTENCE=postgres`, `AUDLI_DATABASE_URL`, `AUDLI_AUTH_MODE=supabase`,
`AUDLI_SUPABASE_URL`, `AUDLI_SUPABASE_PUBLISHABLE_KEY`, `AUDLI_ALLOWED_ORIGINS` (the staging web origin), `AUDLI_LISTENING_ENABLED=true`, `AUDLI_LISTENING_STORAGE=s3`, `AUDLI_LISTENING_S3_*`
(endpoint, region `auto`, access key and secret for the **lessons** bucket, read-only), `AUDLI_LISTENING_AI_DAILY_BUDGET_USD=1.0`, `AUDLI_LISTENING_PROVIDER=openai` (the API process does not call the provider).
**Worker:** the same database and storage settings plus `OPENAI_API_KEY`, `AUDLI_LISTENING_PROVIDER=openai`, and the worker's own recordings-bucket credentials. **Staging web:** `NEXT_PUBLIC_AUDLI_LISTENING=true`, `BACKEND_URL`, the staging Supabase URL and publishable key.
Everything else keeps its default; the feature flags stay off in production.

## 3. Security review: is the design sufficient for an invited beta?
Verdicts are about the code as merged. "Gap" items are required work before the first invited learner; none is optional because the beta is small.
| Area | What exists and was tested | Verdict | Gap and fix before the beta |
|---|---|---|---|
| Learner isolation | every query is scoped by the verified account; another account's ids are indistinguishable from missing (404); tests cover read, answer, upload, skip, retry, library and progress across two accounts, on SQLite and PostgreSQL | sufficient | none |
| Recording keys and bytes | the object key is claimed in the database first, is unguessable (`listening-tmp/<attempt>/<question>/g<n>-<24 random chars>`), is never returned by any API (tests assert it), and the bytes are never served | sufficient | none |
| Recording deletion | deleted when the transcript is saved, on terminal failure, by the worker sweep (abandoned 10 min, anything over 1 h) | sufficient while the worker runs | **bucket lifecycle rule (1 day) is mandatory**: a stopped worker, or an account deleted while a recording is pending (the database row cascades away and the sweep can no longer find the object), would otherwise leave audio behind |
| Object access controls | **implemented, awaiting merge:** recordings have their own store (`build_recording_store`: own bucket, own credentials, own folder locally), `signed_url` and the media route refuse any `listening-tmp/` key, and startup refuses a shared bucket or credential (`AUDLI_LISTENING_RECORDINGS_*`). Tests: `tests/test_listening_storage_isolation.py` | sufficient once merged; the real two-bucket run on staging is still to do | (design:) split into **two buckets with separate tokens** (section 2 item 5). The API signs with a *read-only lessons* token and writes recordings with a *recordings* token; the worker reads and deletes with the recordings token; seeding uses an operator token. Requires a small code change (two stores). Until then a mistaken presign of a `listening-tmp/` key would be readable by the signer's credential. Defence in depth to add in the same change: `signed_url` refuses any key under `listening-tmp/` |
| Signed audio | 5-minute links after an ownership check; tampering and expiry refused (local emulator); Range works locally | sufficient for the lessons bucket once the real-bucket test passes | run the real presign / Range / expiry / tamper test against the staging bucket (never done against a real provider) |
| Database privileges | RLS is on with no policies and PUBLIC/anon/authenticated revoked; the API shares the 1.x process and role. **Worker role implemented, awaiting merge:** `docs/postgres-listening-worker-role.sql` (generated by `app/listening/roles.py`) with matching policies; `prepare(engine, worker=True)`; tested on PostgreSQL 16 by running the whole pipeline under the role and proving it cannot delete, cannot read `users`, answer keys, attempts, responses or any 1.x table, and cannot alter final answers (`tests/test_listening_worker_role.py`) | API: same posture as production 1.x (accepted for the invited beta, documented). Worker: sufficient once merged | (design:) give the **worker** its own role: SELECT on the 2.0 tables it reads; UPDATE on `listening_spoken`, `listening_jobs`, `listening_ai_limits`; INSERT on `listening_ai_limits`; no DELETE; no access to 1.x tables; plus per-table policies for that role (RLS has none, so grants alone return no rows). The worker is the process that holds the provider key, so it is the one worth confining. SQL and a test under a restricted role are the first follow-up PR |
| Provider spend | per-day reservation ceiling, kill switch, per-learner daily cap, retries bounded | sufficient as a bug guard | the OpenAI project limit (item 8) is the real backstop; practise `halt` / `resume` on staging |
| Provider disclosure and retention | the recording screen discloses the external provider and the deletion | partly sufficient | transcripts are kept until the account is deleted; state that retention and review OpenAI's API retention terms in the privacy policy before inviting anyone (a human task) |
| Logs | the worker logs job id, attempt, outcome, seconds only; no audio, transcript, learner id or provider message (tested) | sufficient | confirm Render's request logs do not capture query strings with tokens (the upload URL carries only a random upload id) |
| Request limits | JSON bodies 4 KiB, recordings 5 MiB, type/signature/decode/duration/silence validated before storage | sufficient | none |
| Production exposure | backend flag off by default and fail-closed; web pages 404 unless `NEXT_PUBLIC_AUDLI_LISTENING=true` (verified on a production build) | sufficient | keep both unset in production |

## 4. Order of work (smallest safe sequence)
1. **Done in `feat/audli-2-beta-hardening` (no infrastructure):** two-store storage split with `listening-tmp/` refusal in `signed_url`; worker role SQL (`docs/postgres-listening-worker-role.sql`) with a PostgreSQL test that runs the full pipeline under the restricted role and proves it cannot DELETE, cannot touch `users` or any 1.x table, and cannot alter final answers; a smaller `prepare()` profile for the worker.
2. **You approve and create** items 1-8 (or ask me to script them), in a staging-only workspace. I do not provision anything until you say so.
3. Apply migrations to staging as the database owner: 001-007 (and 008 if production has it), then `python -m scripts.migrate_postgres --listening` (009, 010, 011). Create the worker role. Seed the lessons with `scripts/seed_listening_lesson.py` (development audio first).
4. Deploy staging API, worker and web. Run: the real presign / Range / expiry / tamper test; the recording cleanup test (kill the worker mid-answer, confirm the lifecycle rule and the sweep); a `halt` / `resume` drill; the account-deletion test; the two-account isolation test through the real stack.
5. Run the 12-line iPhone Safari checklist (`docs/LISTENING-M04-PLAN.md`) on the physical device; fix what it finds.
6. Replace the lesson audio with the approved natural voices (new lesson version) and repeat the device run.
7. Go / no-go review for inviting people. Production remains off until a separate decision.

## 5. Rollback
Staging is disposable: delete the services, buckets and database. Production needs no rollback because nothing in production was changed except the merged code, which is inert behind two flags. To turn staging off immediately: `python -m app.listening.ai_budget halt` (no deploy) or unset `AUDLI_LISTENING_ENABLED`.
