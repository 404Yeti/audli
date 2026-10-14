# Audli continuous integration

`.github/workflows/ci.yml` runs on every pull request targeting main and every
push to main. It has no path filters or deployment/migration-to-production jobs.
Actions are pinned by commit SHA; Gitleaks 8.30.1 and actionlint 1.7.12 binaries
are version/checksum pinned. Job permissions are contents-read only, with no
production or paid-provider secrets. Preview deployments for the CI review branch
are suppressed in both existing Vercel configurations.

## Required checks

- **Backend**: Python 3.12, `requirements.txt`, full SQLite regression suite,
  explicit existing provider/storage/onboarding fault-injection cases. Planner
  fault tests run when that module becomes part of the repository.
- **PostgreSQL integration**: PostgreSQL 17 service owned by the ephemeral job;
  full regressions against both SQLite and disposable database fixtures.
- **Rollback schema 003**, **Rollback schema 004**: independently isolated
  PostgreSQL services; real bridge startup/lifespan, preservation, lesson/auth,
  accent and onboarding tests on both schemas. A branch declaring compatibility
  with both ledgers tests its current merge revision. Before the bridge merges,
  and for later schema-004-only applications, these checks use immutable certified
  rollback `4e8514a18a5eb54b81a76d5e53f682c759d239c7`. The full PostgreSQL integration
  check separately tests the current application's own migration runner/schema.
- **Frontend**: Node 22, `npm ci`, units, ESLint, webpack production build, then
  standalone TypeScript (the build generates Next route declaration files).
- **Browser**: Chromium installation and a single serial worker; all ordinary
  mocked browser cases, then separate native HTTP/media acceptance using synthetic
  evidence/transcription and local espeak. Both use the existing test servers;
  no evaluator accuracy or live-provider acoustic acceptance is claimed.
- **Security**: full Git-history secret scan and workflow syntax validation;
  Python audit fails on any known advisory, npm audits for web and marketing fail
  on high/critical advisories, including development dependencies. Lower npm
  severities are visible but non-blocking. Registry/security-feed errors fail the
  check rather than silently passing. There is no license/purchase requirement.
- **CI required**: an always-running gate that fails unless every mandatory job
  succeeds. Failed, cancelled or skipped jobs cannot produce a successful gate.

## Provider and database isolation

Only test-step `PYTHONPATH` loads `ci/sitecustomize.py` with `AUDLI_CI_OFFLINE=1`.
It rejects provider/production credential environment values and non-loopback
PostgreSQL targets before Python can run, then blocks non-loopback DNS/connects.
`ci/test_safety.py` proves the guard, including fail-closed interpreter startup.
Dependencies, Chromium and vulnerability data are installed/fetched before the
test networking restriction; mocked tests do not rely on remote services.
No workflow references production secrets or deploy credentials. Application
schema migrations run only in newly created databases in job-local PostgreSQL.

The secret-scan exception requires both one of the two recovery fixture paths
and the exact `0123456789abcdef` repeated-one-or-two-times fixture value. It does
not exempt test directories, new credentials, historical commits, or whole rules.
Secret output is redacted; failing browser artifacts contain synthetic test data
only and expire after seven days.

## Local commands and release use

Use Python 3.12 (Docker production version) and Node 22. The current local virtual
environment is Python 3.14; actual GitHub checks also verify Python 3.12. Run
`python -m pip install -r requirements.txt`, then tests with
`AUDLI_CI_OFFLINE=1 PYTHONPATH="$PWD/ci:$PWD" python -m pytest -q`. Set
`AUDLI_TEST_POSTGRES_URL` only to an isolated loopback PostgreSQL administrator
when running integrations; never use production. Schema bridge fixtures can set
`AUDLI_TEST_POSTGRES_SCHEMA=4` and run the bridge/lifecycle/auth/accent/onboarding
selection. `bash ci/install-tools.sh /tmp/audli-ci-tools` installs validators;
run actionlint and `gitleaks git --redact --no-banner --log-opts=--all` locally.

In web, use `npm ci`, `npm test`, `npm run lint`, `npm run test:browser -- --workers=1`,
then `AUDLI_INTEGRATED_LESSON=1 npx playwright test --config=lesson-media.playwright.config.ts`.
For native acceptance install Chromium and espeak and create repository `.venv`.
Finally run `npm run build` and `npm run typecheck` sequentially. Do not run browser
servers and production builds against the same generated Next declarations at once.

CI and branch-protection evidence is appended after actual Actions results.
Neither the CI PR nor bridge PR is merged/deployed in this task. Workflows on
unmerged branches execute their PR checks; repository-wide push/main automation
becomes persistent after the CI infrastructure is integrated into main under a
controlled merge that accounts for existing external Render/Vercel auto-deploy.
