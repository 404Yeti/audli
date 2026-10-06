# Persistent learner state (AUD-13)

AUD-12 is the production-origin fix. AUD-13 persistence was production verified by the owner. AUD-14 now supplies authenticated ownership; see [authentication setup and manual verification](AUTHENTICATION.md).

## Architecture

Application/domain code still owns evidence validation, scoring, bounded follow-ups, adaptation, transcript gates and terminal coaching. `app/repository.py` remains the boundary. `SQLProgressRepository` shares operations across SQLite (`ProgressRepository(Path)`) and Postgres (`PostgresRepository`) using SQLAlchemy Core and psycopg. Storage uses no Supabase SDK or service-role key. Supabase Auth is separate from storage; see [AUD-14](AUTHENTICATION.md).

Production requires Postgres; malformed/missing configuration, an unsupported/incomplete schema, or an unavailable database fails startup with a credential-free error. There is no SQLite fallback. The bounded pool checks connections before use; database sessions use UTC, statement/lock timeouts and disabled prepared statements. Keep the existing **single API worker**: conversational/provider checkpoints still use an application lock. Final completion and generation also use database locks/checks to prevent duplicate/stale writes.

Every repository operation scopes ownership to a verified account UUID in Supabase Auth mode. Each protected request gets an independent scope over the shared pool; production rejects the old shared AUDLI_LEARNER_ID. Origin checks remain browser write protection, not authentication. Local development may explicitly use the local learner.

## Schema and retained state

`docs/postgres.sql` is transactional migration **001**, replacing the previously unused reference schema. It uses native UUID keys, foreign keys, UTC `timestamptz`, indexes, bounds/status checks and a `schema_migrations` marker. A composite constraint preserves session/exercise ownership. Production startup validates the version and required columns; it never creates tables automatically.

| Tables | Retained state |
| --- | --- |
| `users`, `learner_profiles` | Name, English target language, goal, interests, target situations, onboarding status, initial listening snapshot, comprehension dimensions, focus, completed attempts and all existing difficulty fields |
| `training_sessions` | Learner, status, UTC start/completion times, starting difficulty snapshot |
| `exercises`, `attempts` | Session association, content/difficulty snapshots, recognized/corrected responses, confidence, uncertainty/source snapshot, attempt status |
| `conversations`, `conversation_followups`, `conversation_turns` | Recoverable checkpoints, pending responses, issued questions/targets/order, accepted answers and attempt associations |
| `turn_assessments`, `assessment_evidence` | Accepted per-turn assessments and queryable unit status/evidence, preserving unknown separately from misunderstanding |
| `evaluation_results`, `adaptation_events` | Final assessment/coverage/coaching, decision, changed variable, previous score, old/new values, reason, policy, difficulty snapshots and timestamps |
| `coach_audio`, `audio_assets` | Versioned coaching references and generated lesson/coaching bytes |

Current state has relational columns. JSONB retains versioned domain snapshots, variable-length interests/situations arrays and string-or-number adaptation values for compatibility and historical context. Snapshots/projections are written together. Final completion atomically saves assessment, adaptation, profile, conversation and session completion. A partial unique index permits one evaluated attempt per exercise; stale adaptation cannot overwrite newer difficulty.

Generated audio is retained in the database so an ephemeral Render filesystem cannot strand a saved lesson. Files are a cache, restored atomically through existing gated routes. **Learner recordings are never stored**; their validation/deletion behavior is unchanged. Recognized/corrected text is retained sensitive learner data. Monitor database growth and backup capacity before wider use; no Supabase Storage setup is required.

## Daily-session and onboarding readiness

Today, one exercise is one session: generation creates it; final assessment completes it. Keep going creates another session. This is **not** daily enforcement or a claim that one exercise lasts 5–10 minutes. Future orchestration can group exercises under the existing session foreign key and move completion to its time/activity budget.

`session_history()` returns UTC-aware timestamps and accepts explicit completion-time bounds (`completed_since`, `before`), rejecting naive datetimes. `session_exercises(session_id)` connects a session to attempt, evidence and adaptation history. No learner timezone/calendar day is inferred. Daily eligibility must later be defined and enforced transactionally on the server.

Onboarding status supports `not_started`, `in_progress`, `profile_saved` and `complete`. Existing typed setup saves `profile_saved`, not full spoken-onboarding completion. The extended existing profile stores interests and target listening situations. The initial listening snapshot stays unknown until a fully evidenced completed assessment. Spoken elicitation, preferences confirmation and the initial assessment workflow remain separate work.

## Configuration

Local defaults:

```dotenv
AUDLI_ENVIRONMENT=development
AUDLI_PERSISTENCE=sqlite
AUDLI_DATA_DIR=data
AUDLI_DATABASE_URL=
AUDLI_LEARNER_ID=
```

Production backend values:

```dotenv
AUDLI_ENVIRONMENT=production
AUDLI_PERSISTENCE=postgres
AUDLI_DATABASE_URL=postgresql://postgres.<PROJECT_REF>:<URL_ENCODED_DB_PASSWORD>@<SESSION_POOLER_HOST>:5432/postgres?sslmode=require
AUDLI_AUTH_MODE=supabase
AUDLI_SUPABASE_URL=https://<PROJECT_REF>.supabase.co
AUDLI_SUPABASE_PUBLISHABLE_KEY=sb_publishable_<PUBLIC_KEY>
# Remove AUDLI_LEARNER_ID in production.
AUDLI_ALLOWED_ORIGINS=https://audli-seven.vercel.app
```

Replace every placeholder. Accepted URI schemes are `postgres://`, `postgresql://` and `postgresql+psycopg://`; host/database/username/password are required. Production requires `sslmode=require`, `verify-ca` or `verify-full`. `require` encrypts transport; prefer `verify-full` with the trusted Supabase CA. For a Render certificate secret file, add `sslrootcert=/etc/secrets/<certificate-file>` to the URI. Authenticated account UUIDs are stable across deploys. Supabase mode rejects a configured AUDLI_LEARNER_ID; old temporary data remains private until a reviewed operator transfer.

All privileged database/OpenAI credentials stay backend-only; the Auth project URL/publishable key are intentionally public. Never place privileged credentials in `NEXT_PUBLIC_*` or put secrets in Git, frontend code, logs, shell history or Linear. Supabase Auth requires a publishable key, documented separately; no service-role key is required. Existing OpenAI/provider settings remain unchanged.

## Exact Supabase and Render steps

These steps initialize a fresh installation. The AUD-13 production project already has Migration 001: for AUD-14, follow [authentication setup](AUTHENTICATION.md) without reapplying the baseline.

1. Create a dedicated Supabase project; retain its database password securely. Keep this database separate from unrelated application tables.
2. In **Connect**, copy the **Session pooler** PostgreSQL URI (port 5432), using its actual user/host and URL-encoding the password. Session pooling supports IPv4 and suits the persistent backend; direct connections require compatible network access. See [Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres).
3. Append `?sslmode=require`, or `&sslmode=require` if a query already exists. Alternatively configure certificate verification with `verify-full` and the trusted CA.
4. In Supabase **SQL Editor**, execute the complete `docs/postgres.sql` once as database owner. Alternatively, configure the backend environment above and run `.venv/bin/python -m scripts.migrate_postgres`; use `python -m scripts.migrate_postgres` inside the container. The command prints applied/already-applied without the URI.
5. Confirm `SELECT version, applied_at FROM schema_migrations;` returns version 1. Confirm tables/indexes and RLS. Migration enables RLS without browser policies and revokes table privileges from PUBLIC and Supabase `anon`/`authenticated`; the backend owner retains access. Do not grant browser access to scripts or learner records.
6. Configure Supabase Auth and confirmed accounts using [AUD-14 setup](AUTHENTICATION.md). Do not set a shared production learner UUID.
7. In Render's backend **Environment**, set the production persistence/auth values above. Retain `OPENAI_API_KEY`, provider/model settings and `AUDLI_DATA_DIR` (a cache in Postgres mode). Install updated `requirements.txt` in the build. The Docker image includes the schema/migration command.
8. Redeploy with this code. Leave Vercel's `BACKEND_URL=https://audli-api.onrender.com` and the existing proxy unchanged. No frontend database credentials are needed; add the public Auth project URL/publishable key described in [AUD-14](AUTHENTICATION.md).
9. Use Vercel to verify setup → introduction/listening → recording/recognition → evidence-aware follow-up → spoken feedback → adaptation → transcript unlock → Keep going. Confirm uncertain/failed attempts leave difficulty unchanged and failed feedback audio preserves assessment.
10. Restart/redeploy Render. Verify the same profile/difficulty, current exercise/conversation and history reload; playback must work with a fresh cache. Complete another activity and confirm persisted difficulty is used. Inspect session timestamps, evidence and adaptation reason in SQL Editor, without exposing private learner text.

Hosted Supabase connectivity, TLS/CA behavior and this Render restart/browser check must be manually verified before marking the issue Done. Local PostgreSQL tests do not establish these results.

## Existing SQLite migration/import

Local SQLite upgrades additively on first use. The adapter creates a consistent `<database>.pre-postgres.sqlite3` backup, then atomically backfills fields, sessions and available evidence. Existing learner state, results and checkpoints are retained. Old named/completed learners become `profile_saved`; missing historical intermediate assessments are not fabricated. Keep backups private. If migration fails with a backup present, inspect/restore it before retrying; backup overwrite is refused.

Historical completion timestamps use the persisted adaptation-event time, rather than the earlier recording time. Newly introduced projection timestamps with no original historical counterpart represent migration time; they must not be treated as proof of when an old question was issued.

Before a production transfer, stop training writes and take a consistent copy of the existing SQLite database **and generated `exercise_audio/` files before an ephemeral restart**. Preserve their layout: `audli.sqlite3` beside `exercise_audio/`. Initialize Postgres and verify the target authenticated account UUID, then run:

```bash
.venv/bin/python -m scripts.import_sqlite /private/backup/audli.sqlite3 --target-learner-id <VERIFIED_ACCOUNT_UUID>
```

The source is upgraded locally if needed. State/history/checkpoints and available generated audio are copied in one target transaction. The target must have a default empty profile and no exercises; existing progress is not overwritten/merged. Use `--source-learner-id <id>` for a scoped SQLite learner. Selecting Postgres alone does not transfer data. Already-lost audio cannot be recovered. Verify the import before deleting backups.

If someone previously applied the old reference Postgres schema, back it up and prepare a reviewed additive migration/import. This baseline fails on existing/incompatible tables; it never drops them. Later changes must add numbered migrations and advance version handling, not edit an applied baseline.

## Testing and remaining work

Normal tests isolate storage environment settings (including import-time app startup), use temporary SQLite and mocked providers, and cannot inherit production persistence from a shell or `.env`:

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q app tests scripts
cd web
npm test
npm run lint
npm run build
npm run typecheck
```

For real disposable local Postgres, an owner must be able to create/drop test databases and roles:

```bash
AUDLI_TEST_POSTGRES_URL=<DISPOSABLE_LOCAL_CLUSTER_URI> .venv/bin/pytest -q tests/test_persistence.py
```

Never use production Supabase credentials here. Tests create/drop `audli_test_*` databases and a temporary untrusted role. Coverage includes native Postgres types/migrations, restart/cache recovery, evidence history, atomic/duplicate/concurrent completion, stale adaptation, ownership, import refusal and RLS denial. Mock AI tests establish plumbing, not evaluator accuracy.

Remaining work after AUD-14 manual authentication verification: daily eligibility/timezone policy and a 5–10 minute session budget; full spoken onboarding; retention/export/deletion policy, backups/restores, generated-audio capacity and operational monitoring. Provider checkpoints still assume one API worker, and Keep going remains unrestricted.

Recommended next issues: **AUD-15 spoken onboarding** and **AUD-16 controlled daily sessions**, after AUD-14 review/verification. Daily-session orchestration and spoken onboarding can then use this persisted state.

## AUD-13 historical verification recorded on 2026-10-06

| Check | Result |
| --- | --- |
| Complete backend suite | 230 passed |
| Postgres-enabled persistence suite | 31 passed, including nine cases on isolated PostgreSQL 18.6 |
| Production-environment isolation check | 22 persistence tests passed with deliberately unreachable production settings in the shell; no production database connection was attempted |
| Frontend helpers | 25 passed |
| Frontend lint, production build, standalone typecheck | Passed |
| Python compilation; `git diff --check` | Passed |
| Hosted Supabase / Render restart / real browser learning loop | Subsequently verified by the owner; AUD-14 Auth verification remains pending |

The first sandboxed full run stalled in an existing audio subprocess test and was stopped. The complete retry outside the sandbox passed. PostgreSQL socket access also required an unsandboxed test run; the disposable server was stopped after verification. No live Supabase or paid AI tests were run, and no production environment was changed.

Files changed: `.env.example`, `AGENTS.md`, `Dockerfile`, `README.md`, `requirements.txt`; `app/config.py`, `app/main.py`, `app/models.py`, `app/repository.py`, `app/conversation_api.py`, `app/diagnostics.py`; `app/storage/__init__.py`, `schema.py`, `repository.py`, `adapters.py`, `migrations.py`; `docs/postgres.sql`, `docs/PERSISTENCE.md`; `scripts/migrate_postgres.py`, `scripts/import_sqlite.py`, `scripts/inspect_attempt.py`; `tests/conftest.py`, `tests/test_persistence.py`, `tests/test_diagnostics.py`, `tests/test_security.py`, `tests/fixtures/sqlite_v0.2.sql`. No frontend source changes.
