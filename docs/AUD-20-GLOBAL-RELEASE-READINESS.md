# AUD-20 global production release

**AUD-20 GLOBAL RELEASE LIVE.** Conversational accent practice is enabled for all authenticated learners. PR [#13](https://github.com/404Yeti/audli/pull/13) merged as `ec1672b8d5cfe5b84eda9afcaa58c5f4e9ddad09`. Render deployment `dep-db5jnud9mjac738hvql0` became live at 2026-10-11 07:35:09 UTC. Vercel production deployment `6992528929` succeeded on the same application revision. See [production evidence](AUD-20-GLOBAL-PRODUCTION-RELEASE.json).

## Approval and audio verification

The product owner explicitly approved Daniel/British, Clara/Australian and Jessica Gallagher/Irish and waived mandatory numerical ratings. The validator accepts the auditable [owner decision](AUD-20-PRODUCT-OWNER-APPROVAL.json); the blank CSV has not passed and no ratings were fabricated. Existing source audio and hashes are preserved and validated in the deployment image. Technical accessibility, model compatibility and integrity remain required.

| Accent | Voice | Approved ID |
| --- | --- | --- |
| British | Daniel | onwK4e9ZLuTAKqWW03F9 |
| Australian | Clara | tyepWYJJwJM9TTFIg5U7 |
| Irish | Jessica Gallagher | DbwWo4rVEd5NrejHYUnm |

Three explicitly authorized real ElevenLabs calls passed: **1,416 combined characters**, one per voice, zero retries, existing credits and zero overage. All audio decoded; saved Replay was identical and consent consumed. Flash v2.5 was used at British/Irish 0.75 and Australian 1.0 speech rates. Initial validated rate coverage is 0.75 and 1.0; other rates use ordinary listening without altering difficulty. This was isolated service verification with real provider audio, not authenticated production HTTP playback. [Live evidence](AUD-20-LIVE-VOICE-VERIFICATION.json). The three-call verification authorization is exhausted; no additional test synthesis is authorized.

Audli's tutor voice remains `yKYzqEa22xh5PdidhN70`; its service and voice settings were preserved. Safe production startup logs confirm the custom ElevenLabs tutor selected, model Flash v2.5 and original stability/similarity/style settings. Accent routing changes listening passages only.

## Database and compatibility

A fresh private public-schema backup was created at 07:22 UTC and restored in isolated PostgreSQL **17.11**. All 17 table fingerprints matched the exported source snapshot. It contains application learner data and generated audio, excludes managed Supabase Auth/storage, and is stored outside the repository with mode 0600. SHA256: `5989dea9fb84f09c250740975a8d764d847f2365ec5a8a4671cf50f18f5111a0`. [Backup evidence](AUD-20-BACKUP-VERIFICATION.json).

Migration 003 applied at **07:23:55 UTC**, while bridge `2454f04` was running. Schema history is `[1,2,3]`; all existing learner-table fingerprints were preserved. The new controls initially had disabled flag/zero budget. RLS and browser-role revocations passed. Bridge remained healthy: 19/19 safe production checks after migration. Upgrade, restart and rollback were also verified against isolated PostgreSQL 17.11. [Migration evidence](AUD-20-PRODUCTION-MIGRATION.json), [bridge verification](AUD-20-SCHEMA-BRIDGE.md).

## Active shared controls

At 07:44 UTC the shared PostgreSQL flag was enabled with **5,000 characters per UTC day**, **one concurrent synthesis**, **4,000 characters per passage**, provider timeout **20 seconds** and uncertain-outcome lease **90 seconds**. Environment limits are additional ceilings; there is no account allowlist. Atomic reservations, permanent passage-scope deduplication and retained counters survive process restart. Budget exhaustion, unavailable controls, unsupported accents/rates and provider failure use ordinary listening.

The session pooler discarded libpq startup timeout options. Every control transaction now applies transaction-local statement_timeout=750ms, lock_timeout=250ms and idle_in_transaction_session_timeout=2s. This was verified against production PostgreSQL. Flag reads have no positive cache and a two-second asynchronous deadline; stale/unavailable results fail closed.

The enable→disable→enable check observed committed disable in **897.918 ms** across two independently pooled release-code controllers against production PostgreSQL. This is an actual shared-row transition measurement, **not** a measurement across two deployed authenticated HTTP instances. Local two-instance PostgreSQL tests also passed. The flag is enabled after the successful test. [Activation evidence](AUD-20-PRODUCTION-ACTIVATION.json).

ElevenLabs subscription checks at 07:43 and 07:45 UTC showed active Creator subscription, 93,808/121,314 credits used, **27,506 remaining**, maximum credit extension zero and can_extend_character_limit=false. Credits did not increase during that observed window. Provider overage is disabled. Application characters are admission counters, not actual billed credits; tutor/external calls share the included quota. A cancelled/timed-out remote request may finish and consume credits, and a lease expiry cannot prove remote work stopped. No automatic accent retries; conservative reservations are retained. [Provider evidence](AUD-20-PROVIDER-SPENDING-CHECK.json).

## Consent and learner behavior

Remembered interest and one-passage consent are separate states. Existing/new learners may discover preferences verbally during the existing check-in. Discovery refusal persists and discovery/offers are bounded. Saved preferences never activate a passage. Only explicit acceptance or an independently requested supported accent creates consent for the current passage. Refused, ambiguous, expired, cancelled and unsupported choices return to ordinary listening; no repeat offer after a session decline. Learners may revoke consent before dispatch. Server-side dispatch rechecks ownership, lesson scope, consent and control revision. Replay/Retry reuse previously saved authorized passage audio without re-synthesis or future consent. Difficulty, scoring, progress, topic selection and transcript gates are unchanged. No accent selector or onboarding screen was introduced. See [consent state machine](AUD-20-CONSENT.md).

## Verification and monitoring

- Backend: 820 distinct passing cases across SQLite/isolated PostgreSQL after the two response-field assertions were updated; overlapping reruns are not added as unique cases. Final pooler/fleet/consent/global suite: 121 passed, two expected skips. It covers atomic fleet limits, failure/timeout/restart handling, authentication, ownership and consent.
- Frontend: 58 unit cases passed; TypeScript, ESLint and production build passed. Existing production frontend behavior was preserved.
- Dependency checks: npm production audit zero vulnerabilities; pip check no broken requirements.
- Production-safe checks before and after activation: **19/19 passed**, including backend/frontend health, Supabase auth configuration, missing/invalid-token rejection and origin restrictions. These checks do not establish successful authenticated playback. [Health evidence](AUD-20-PRODUCTION-HEALTH.json).
- Render logs from 07:35:10 through 07:45:42 UTC: **zero application error logs and zero request 5xx logs**, no pagination remaining. Shared production usage at 07:45:22: zero reserved characters, zero reservation rows. This is a bounded observation with no demonstrated learner accent traffic, not a load test or long-term reliability claim. Existing Render logs and content-free control counters provide monitoring; no new alert rule was configured.

## Accepted verification gaps

The owner explicitly deferred dedicated two-account authenticated production acceptance. Successful production passage/tutor playback and real cross-account isolation remain **unverified**, with automated ownership/authentication/consent checks retained. Live real-provider checks used isolated synthetic learner state. Production propagation was measured with two control pools, not two authenticated deployed instances. Real multi-user production load was not exercised. These are accepted risks, not successful tests, and no authentication or isolation control was weakened.

## Emergency disable and rollback

From an authorized backend shell using its protected environment, run:

```sh
python -m scripts.accent_controls disable
python -m scripts.accent_controls status
```

This updates the shared flag without restart, increments its revision and stops new accent dispatches. Ordinary listening remains available; saved audio Replay remains available. Already dispatched provider work can finish. Do not clear reservations or reset counters. Database unavailability fails closed; investigate connectivity without deleting state.

If application rollback is required, disable first and redeploy the compatible bridge **`2454f040b693ee5b53ebca1fad5574d4460d8a7c` or a later schema-3-compatible revision**. Keep schema 3 and learner data; no database downgrade/reset is required. **Never roll back below this bridge after migration 003.** The bridge accepts schemas 1,2,3 without requiring the new tables. Unknown future schema versions remain rejected. Do not restore the pre-migration backup over a live database as a routine feature rollback; backup recovery is a separate incident procedure preserving subsequent learner changes.

For re-enable after an incident, verify approval hashes, provider no-overage restrictions, budget headroom and health, then use the privileged existing CLI with the production environment:

```sh
python -m scripts.accent_controls enable --daily-characters 5000 --concurrent 1
python -m scripts.accent_controls status
```

Do not run additional paid verification without new authorization. See [active configuration](AUD-20-GLOBAL-CONFIG-PROPOSAL.json) for the eight non-secret deployment settings and exact approved mapping. Credentials and backups remain outside the repository.
