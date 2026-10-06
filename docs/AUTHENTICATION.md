# Authenticated learner identity (AUD-14)

AUD-13 production persistence was manually verified by the owner. AUD-14 adds account identity and isolation; it remains In Progress until review and manual production verification. No production settings or database are changed by implementation/tests.

## Architecture and trust boundary

The current client is a client-rendered Next.js lesson screen, served by Vercel with a same-origin `/api/*` rewrite to Render. Use Supabase Auth email/password accounts through `@supabase/supabase-js` in the browser. Its public project URL and **publishable** key are browser configuration, not database credentials. No service-role/secret key is needed. Browser database access remains denied by the existing RLS/no-policy schema and revoked privileges.

The SDK handles confirmation redirects, browser session persistence and automatic refresh. API helpers obtain an access token from the SDK and send `Authorization: Bearer <token>` through the existing Next.js rewrite. FastAPI does **not** trust SDK user objects, decoded JWTs, cookies, URL parameters, form fields or `X-Learner-Id`. On **every protected request** it calls the configured project's `GET /auth/v1/user`, using the publishable key and bearer token, and requires a confirmed, non-anonymous user with a valid UUID. This lets Supabase validate signing algorithms, expiration and project identity without maintaining another JWT/JWKS implementation. See [Supabase user verification](https://supabase.com/docs/reference/javascript/auth-getuser) and [session handling](https://supabase.com/docs/reference/javascript/auth-getsession).

Invalid/missing/expired credentials return 401 with `WWW-Authenticate: Bearer`. Auth outages, throttling and unexpected responses fail closed with 503; they never select a local/shared identity. Only `/api/health` and `/api/auth/config` are public. The latter returns only `local` or `supabase`, never keys. LocalRequestGuard and its exact body limits/replay/origin comparisons remain in place, ahead of authentication. There is no new CORS middleware; production uses the same-origin Next.js proxy.

After verification, a new request-scoped `SQLProgressRepository` uses the verified user UUID and the existing shared engine/pool. An ASGI context variable binds existing route closures to that scope; its token resets in `finally`. The shared pool owner never has its learner ID mutated. User/profile initialization uses transactional conflict-safe inserts; a returning user is never reset. No additional database connection pool is created per account/request.

Repository ownership predicates cover profiles, sessions, exercises and audio assets directly and attempts, results, adaptation and conversations through their parent exercise. Nested writes additionally verify pending/accepted attempts and issued follow-up IDs belong to the same exercise. Foreign ownership returns the same 404 as an absent resource at the API boundary. Transcript and coaching gates are unchanged. Even an existing filesystem cache is checked against the account's audio ownership before playback.

Audio requests use authenticated fetch and disposable object URLs because `<audio src>` cannot attach bearer headers. Tokens never enter audio URLs or query parameters. Blob URLs are revoked on resource changes/unmount; recordings, speech synthesis and playback stop when the private lesson unmounts. Sign-out clears browser credentials even if the Auth service is unavailable. 401 unmounts the lesson and requests sign-in; 503 permits retry without clearing a valid account session. Account switches key the lesson by user ID to discard the previous account's state. An expired token can refresh through the SDK before a request; an invalid/rejected session requires sign-in.

## Schema and legacy identity

**Migration 001 is unchanged. No Migration 002 is required.** `users.id` already has a native UUID key and all learner relationships exist. Verified Supabase user IDs now populate that existing key. No automatic foreign key to Supabase's managed `auth.users` is added: the repository continues to work on ordinary Postgres and deterministic SQLite, and application data retention/deletion remains an explicit future policy.

Remove `AUDLI_LEARNER_ID` in authenticated mode; startup rejects it rather than silently sharing data. Existing temporary-learner rows remain intact but inaccessible to new accounts. They are **never automatically claimed by the first signup**.

If the temporary learner's history belongs to one known person, a trusted operator may explicitly transfer it after pausing all training writes, backing up the database and verifying the person's account UUID in Supabase **Authentication → Users**. If the shared profile contains several people's data, do not transfer it to an arbitrary account; resolve ownership separately. The operator-only tool is not an HTTP route and requires backend database credentials:

```bash
# With the NEW authenticated backend environment. No database writes in preview.
python -m scripts.transfer_legacy_learner --legacy-learner-id <OLD_CONFIGURED_UUID> --account-user-id <VERIFIED_ACCOUNT_UUID>
# Only after reviewing entitlement, backup, UUIDs and preview:
python -m scripts.transfer_legacy_learner --legacy-learner-id <OLD_CONFIGURED_UUID> --account-user-id <VERIFIED_ACCOUNT_UUID> --apply
```

It refuses a non-empty target, uses one transaction, preserves session/exercise/attempt IDs, difficulty, evidence, adaptation timestamps and generated audio, and maintains the existing composite foreign keys throughout. The old identity loses access. The transfer is not rerunnable after success; repeat attempts fail safely. Nothing runs automatically on startup/deploy. The SQLite import tool similarly requires `--target-learner-id <VERIFIED_ACCOUNT_UUID>` in Supabase mode. Inspection tooling remains local-only; do not use a browser UUID to authorize an administrative transfer.

## Required production configuration

Supabase project:

1. Reuse the AUD-13 project and its existing Migration 001 database. Do **not** reapply the baseline or grant browser table policies/privileges.
2. Under **Authentication → Sign In / Providers**, enable Email/password and email confirmation. Leave anonymous sign-ins disabled for Audli.
3. Under **Authentication → URL Configuration**, set Site URL to `https://audli-seven.vercel.app`. Add exactly that confirmation redirect URL (and exact explicitly approved development/custom-domain URLs as needed); avoid wildcard production redirects. The signup callback returns to Home, where the SDK consumes the session before lesson API calls.
4. Configure production SMTP/email delivery and rate limits before general signup. Use Supabase's password policy (at least eight characters); the sign-up form mirrors the minimum. Review JWT lifetime; short-lived access tokens limit the logout window described below.
5. Copy the project HTTPS URL and the `sb_publishable_...` key from **Connect / API Keys**. Audli deliberately accepts publishable keys, not legacy anon JWTs or service-role/`sb_secret_...` keys. Create/retrieve a publishable key if the project currently only uses legacy keys.

Render backend environment:

```dotenv
AUDLI_ENVIRONMENT=production
AUDLI_PERSISTENCE=postgres
AUDLI_DATABASE_URL=<KEEP_THE_EXISTING_PRIVATE_SSL_POSTGRES_URI>
AUDLI_AUTH_MODE=supabase
AUDLI_SUPABASE_URL=https://<PROJECT_REF>.supabase.co
AUDLI_SUPABASE_PUBLISHABLE_KEY=sb_publishable_<PUBLIC_KEY>
AUDLI_ALLOWED_ORIGINS=https://audli-seven.vercel.app
# DELETE AUDLI_LEARNER_ID entirely (or leave empty), retaining its old UUID privately
# only if a reviewed legacy transfer is needed.
```

Keep existing OpenAI/model/data-directory settings, TLS settings, one API worker and database access unchanged. All privileged database/OpenAI credentials stay backend-only. Render must reach the configured HTTPS Supabase Auth endpoint; verification has a bounded timeout and never follows redirects. Incorrect/missing auth/database configuration fails startup, without an SQLite/shared-learner fallback.

Vercel frontend environment (set for the intended production environment and rebuild):

```dotenv
BACKEND_URL=https://audli-api.onrender.com
NEXT_PUBLIC_SUPABASE_URL=https://<SAME_PROJECT_REF>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_<SAME_PUBLIC_KEY>
```

`NEXT_PUBLIC_*` values are embedded at build time and intentionally public. Never set a database URI, service-role key, `sb_secret_...` key or OpenAI key in the frontend. Missing/wrong frontend auth configuration shows a connection/configuration recovery state; it does not mount an unauthenticated lesson. A publicly hosted production frontend also refuses a backend reporting local identity mode; loopback build/Docker previews may explicitly use the development backend. Configure and release both services together in a maintenance window: an old frontend will receive 401 from the new protected API, and the new frontend must not be connected to the old unauthenticated backend. Changing dashboard settings/deploying remains an owner action.

## Local development and tests

Default `.env.example` is explicitly development-only: `AUDLI_ENVIRONMENT=development`, `AUDLI_AUTH_MODE=local`, SQLite, and a single local learner. FastAPI/Next.js development commands bind to loopback; local Docker publishes only loopback ports. Do not expose this mode publicly. Local Postgres mode still requires an explicit development `AUDLI_LEARNER_ID`. Production validation prohibits local identity mode regardless of origins.

To exercise authentication locally, set `AUDLI_AUTH_MODE=supabase`, the project URL/publishable key on the backend, and both public frontend values in `web/.env.local`; keep `AUDLI_LEARNER_ID` empty. SQLite may still store separate verified users in development, or use a dedicated development Postgres database. Add the exact loopback frontend URL to Supabase's redirect allowlist. Never use the production database for automated tests.

The normal test suite resets storage/auth environment before app import and uses isolated SQLite, mocked Auth responses and synthetic provider fixtures. Opt-in tests use disposable local Postgres databases and the unchanged migration:

```bash
.venv/bin/pytest -q
AUDLI_TEST_POSTGRES_URL=<DISPOSABLE_LOCAL_CLUSTER_URI> .venv/bin/pytest -q tests/test_persistence.py tests/test_auth.py
.venv/bin/python -m compileall -q app tests scripts
git diff --check
cd web
npm test
npm run lint
npm run build
npm run typecheck
npm run test:browser
```

Browser tests start their own dev server with **fake** public project/key configuration and intercept all Auth/API requests. They do not authenticate to a real project. Tests cover missing/invalid/expired/unconfirmed/anonymous tokens, outage, duplicate headers, initialization, returning state, concurrent scopes, profile/history/session/resource isolation, guessed IDs, nested writes, cached and restored audio, bounded evidence follow-ups, adaptation/restart, operator transfer refusal, signup confirmation, sign-in/logout/reload, expired-session recovery, bearer helpers and connection failure. Mock Auth tests establish integration plumbing; actual hosted signing/expiry/proxy behavior requires the manual checks below.

## Exact manual production verification

After reviewing code and approving the release, the owner should:

1. Back up the existing database and save the old temporary learner UUID privately. Set the Supabase/Render/Vercel values above; confirm production starts with no `AUDLI_LEARNER_ID` and no schema migration is requested.
2. In a fresh browser, confirm Home shows Sign in/Create account, not lesson data. Request `/api/profile`, `/api/history`, an existing exercise audio URL and its conversation URL without an Authorization header: all must return 401 with no-store. Health/auth-config should still work without credentials.
3. Create confirmed account A and account B using separate browser profiles/incognito contexts. Confirm email redirect returns only to the configured Audli origin. Test wrong password and an unconfirmed account; neither must gain learner API access.
4. Sign in as A. Save a name/goal and complete a listening exercise including an evidence follow-up, spoken coaching and transcript unlock. Note exercise/attempt/follow-up URLs in local developer tools, avoiding copying tokens into logs/issues. Confirm API **and audio** fetches carry a bearer header and URLs contain no tokens.
5. Sign in as B. Verify its fresh profile, difficulty, current exercise and history are independent. Using B's own Authorization header in developer tools or an ephemeral local API client, try A's exercise audio, cached coach-audio, transcript, conversation, listened/ready/coach-audio writes, recording upload, attempt result, assess and legacy evaluate URLs: each must return 404. Supplying A's learner ID in a header/query must not change B's scope; adding it to typed profile JSON must fail validation. Confirm A's state/evidence/difficulty are unchanged.
6. Reload/close/reopen A's browser, then restart Render. A must retain its profile, evidence/history/difficulty and current lesson; generated playback must work after the filesystem cache is cleared. The next activity must use A's persisted adaptation. B must remain isolated.
7. Sign out during recording or playback. The microphone indicator/playback must stop, private lesson state must disappear, and reload must remain signed out. Test a rejected/expired token (including an expired token copied before refresh): protected requests return 401 and the browser returns to sign-in. A normal refreshable SDK session should refresh and continue. Tokens from a **different test Supabase project** must also be rejected by Render's configured project.
8. Temporarily simulate Auth/API unavailability using local browser request blocking, not production config changes. Verify recovery/retry; no shared/local profile appears. Test an unconfigured/misleading Origin write still returns 403 and oversized bodies still return 413.
9. If justified, review the trusted operator transfer preview separately, then approve/apply it manually and recheck A's old history/audio/IDs after restart. Never transfer shared multi-person history merely to make a demo look continuous.
10. Record outcomes and limitations on AUD-14. Leave it In Progress until all manual checks/review requirements are satisfied; implementation does not mark it Done.

## Security assumptions and remaining work

TLS and the configured Supabase Auth service are trusted. The backend database role is privileged: app ownership predicates are the isolation boundary, backed by foreign keys and regression tests. Existing RLS without browser policies protects direct browser access; it does not scope the database owner's queries. No auth.users cascade/delete policy is introduced.

The browser SDK stores access/refresh credentials in local storage. Protect the frontend against XSS, third-party script injection and shared-device access; no cookies/server-rendered private content are introduced in this client-rendered application. A deliberate sign-out removes local credentials and revokes that refresh session, but **already issued access JWTs may remain usable until expiration**: Auth user verification is not an immediate token revocation list. See [Supabase session semantics](https://supabase.com/docs/guides/auth/sessions). Use an appropriate JWT lifetime; instant access-token revocation/MFA and cookie-based SSR hardening are separate work.

Each protected request incurs an Auth-network round trip; Auth outages fail closed. No token cache or offline fallback is introduced. A deleted/replaced account does not automatically delete or transfer its retained learner data. Account deletion/export/retention and password recovery UI are not implemented. Abuse controls/quotas and SMTP operational readiness are still needed before unrestricted public signup; auth alone does not cap OpenAI usage.

One API worker and the existing application lock still govern provider/conversation checkpoints; authentication does not change those persistence/concurrency invariants. Spoken onboarding is AUD-15; daily eligibility, timezone policy and session budgets are AUD-16. Neither flow, daily lockout nor a UI redesign is implemented here.

## Local verification recorded on 2026-10-06

| Check | Result |
| --- | --- |
| Complete backend suite | 266 passed |
| PostgreSQL-enabled auth/persistence suite | 95 passed on disposable PostgreSQL 18.6 |
| Transfer after final transaction-lock refinement | 2 passed (SQLite and PostgreSQL) |
| Production-configured origin/body regression suite | 55 passed |
| Frontend helper suites | 30 passed |
| Chromium browser suite | 17 passed (mock Auth/API, including refresh and recording disposal) |
| Frontend lint, production build, standalone typecheck | Passed |
| Python compilation (app/tests/scripts); git diff --check | Passed |
| npm audit --omit=dev | 0 production vulnerabilities; install audit reports 5 existing development vulnerabilities, not changed in this issue |
| Hosted Supabase Auth / Vercel proxy / Render restart | Pending owner verification |

Migration 001 is byte-identical (SHA-256 `f6e51fc8983b97d32f775142b0a996c7ca8a68ca9be9ba81c0df8e544143b1cf`). No Migration 002 was created. The isolated Postgres server was stopped after tests. Browser tests use port 3100 and a separate ignored build directory to avoid the owner's existing port-3000 server; the initial port collision was resolved without stopping that server. Next.js generated route references were restored to avoid unrelated generated-file changes. No Supabase, Render or Vercel settings/data were modified, and no commits, pushes or deployments occurred. AUD-14 remains In Progress.

Changed files:

- `.env.example`
- `.gitignore`
- `AGENTS.md`
- `README.md`
- `app/auth.py`
- `app/config.py`
- `app/main.py`
- `app/storage/adapters.py`
- `app/storage/repository.py`
- `docs/AUTHENTICATION.md`
- `docs/PERSISTENCE.md`
- `scripts/import_sqlite.py`
- `scripts/migrate_postgres.py`
- `scripts/transfer_legacy_learner.py`
- `tests/conftest.py`
- `tests/frontend.test.mjs`
- `tests/test_auth.py`
- `tests/test_diagnostics.py`
- `tests/test_security.py`
- `web/app/globals.css`
- `web/app/page.tsx`
- `web/components/auth-gate.tsx`
- `web/eslint.config.mjs`
- `web/lib/api.ts`
- `web/lib/auth.ts`
- `web/next.config.ts`
- `web/package-lock.json`
- `web/package.json`
- `web/playwright.config.ts`
- `web/tests/auth.spec.ts`
- `web/tests/lesson.spec.ts`
- `web/tsconfig.json`

## Post-review account-switch correction

Lesson work now starts with a `LessonLifetime`/`LessonOperation` bound to the current authentication generation. Logout, session rejection, a different account/session, component unmount, and explicit operation cancellation invalidate pending work. Requests carry an abort signal and recheck ownership of the operation after token acquisition, response arrival and body parsing. The lesson also checks continuation validity before state changes, another request, recording startup or playback. Late media streams are stopped; late audio URLs are revoked. Ordinary token refresh preserves the Supabase session ID and does not cancel a stable learner's lesson.

Browser regression tests reproduce setup permission resolving after A logs out and B logs in: no profile write, exercise generation or request using B's token occurs, and B's setup remains unchanged. Separate cases cover delayed exercise generation and delayed recording acquisition. Helper tests cover cancellation, rejected sessions, response parsing, late audio bodies, delayed token acquisition and prevention of new operations on an old lifetime.

Aborting a browser request cannot recall work already received by FastAPI. Such work remains authorized under the original account and may complete durably; a stale frontend continuation cannot adopt a later account's credentials. This correction changes no backend authorization, persistence, origin/body limits or schema. Hosted configuration/manual verification remains pending; AUD-14 stays In Progress.

Verification after the correction: complete backend suite 266 passed; disposable PostgreSQL auth/persistence suite 95 passed; auth/origin/body/diagnostics regression suite 97 passed; frontend helpers 38 passed; Chromium browser suite 20 passed. Frontend lint, production build and typecheck, Python compilation, and git diff --check passed. npm audit --omit=dev reported zero vulnerabilities. The local test Postgres cluster was stopped and generated Next route references were restored; production was not touched.
