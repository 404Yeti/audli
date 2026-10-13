# AUD-38 controlled production release

Owner approval:explicit listening approval and production release authorization for all11 IDs,2026-10-13. No additional paid production synthesis authorized. All11 evaluated samples matched registry IDs and SHA-256 receipts before inclusion in the release evidence. No tutor/Clara replacement or billing change.

## Known-good rollback baseline

Git revision `609529235b8badea8403fd8f763f69cc004a0b79` confirmed live on Render and Vercel before release. Render baseline deployment `dep-db6dpc3rjlhs73blb4tg`, service `srv-db270erbc2fs73fg85gg`, Robert's confirmed workspace `tea-dauqlhd9fdbs739vmkv0`. Vercel baseline production `https://audli-lms7opns5-robert-leybas-projects.vercel.app`, GitHub deployment7014919974, success. Production origins `https://audli-api.onrender.com` and `https://app.audli.ai`.

## Deployment order

1. Pass final SQLite/PostgreSQL/backend, frontend build/lint/typecheck/unit and browser verification. Commit only scoped registry, interpretation, preference/schema/configuration, evidence and regression changes from a clean worktree on production main.
2. Open/merge focused release PR. Existing main-branch integrations automatically deploy Render backend and Vercel frontend; do not create a duplicate code deployment.
3. Confirm both deployment revisions match release commit. Before feature activation, health remains successful and legacy voices/configuration remain available.
4. Merge only `AUDLI_EXPANDED_ACCENT_LIBRARY_ENABLED=true` into the existing Render environment; preserve all secrets, legacy mappings, tutor and fleet budgets. If saving does not automatically deploy runtime settings, trigger one configuration-only redeploy after the existing code deployment finishes.
5. Verify health reports exact release revision and all14 available passage accents; auth/config is Supabase; unauthenticated/invalid-token learner routes and preference writes fail closed; CORS and transcript gates remain enforced. Inspect runtime/deployment logs and durable fleet settings without retrieving learner text or raw audio.

No production paid synthesis. Local fixture tests establish consent/routing/fallback/replay/adaptive0.8 behavior; owner-reviewed smoke samples establish selected-voice audition evidence. Full live spoken STT→consent→paid passage→replay and actual production-key quota fallback remain unverified until separately authorized or owner-tested.

## Safest rollback

For a routing/consent/health failure first set `AUDLI_EXPANDED_ACCENT_LIBRARY_ENABLED=false` on the existing Render service (merge, never replace all envvars), deploy runtime settings and verify only legacy three mappings remain. This removes the11 automatically added mappings while preserving existing budgets, kill switch, learner data and British/Irish/Clara support. Never delete or reset production tables/audio.

If code rollback is required, revert the focused AUD38 release commit through the usual PR workflow and let main integrations redeploy. Alternatively select the exact known-good revision609529235b8badea8403fd8f763f69cc004a0b79 on Render and promote the baseline Vercel deployment above. Keep expansion flag false. Confirm deployed revision, health, Supabase auth denial/ownership, legacy mappings and logs. No schema migration is part of AUD38; rollback requires no data migration. Existing global fleet disable remains the emergency all-accent kill switch if consent safety is in doubt; prefer expansion-only disable when legacy behavior is healthy.

## Verification evidence

Final counts, release commit/PR and deployment/production receipts will be appended after success. AUD38 remains In Progress until those checks succeed.

Predeployment evidence:full SQLite/disposable PostgreSQL17.11 backend1321 passed,7 backend-specific skips; final expansion/health checks2 passed. Focused expansion/preferences/intent SQLite+PostgreSQL268 passed. Browser standard cases137 passed across the warmed-suite run and corrected-origin password case; integrated real HTTP/media case1 passed (138 browser cases total). Frontend58-unit-test suite, TypeScript, ESLint and production Webpack build passed. Read-only ElevenLabs access/model check:all11 exact IDs HTTP200, English Flash2.5 supported;0 paid calls. Read-only production fleet control enabled,daily5000,concurrent1; preserved.

## Production outcome

Release PR[24](https://github.com/404Yeti/audli/pull/24) merged as `b4726d4fed43677ca759531dc547945aa5b1ea2c`. Render code deployment `dep-db7324navr4c73c15d10` and configuration-only activation `dep-db735o6k1f9s73c2ra7g` live. Vercel production deployment7039711837 succeeded at https://audli-li3o9bv81-robert-leybas-projects.vercel.app for the same commit. Both direct API and primary app routes report that exact revision.

Merged only expansion flag=true; no secret,voice,budget,concurrency,billing or database configuration changes. All14 actual production mappings exactly match registry; tutor yKYzqEa22xh5PdidhN70/providerElevenLabs, existing normal passageCoral, Flash2.5 and budgets5000 daily/1 concurrent preserved. Durable fleet control unchanged.

Non-billable production checks18/18 passed before activation and18/18 after activation:health/authmode, unauthorized/invalid-token reads and preference writes, transcript denial and hostile-origin write rejection. No production authenticated learner mutation or paid synthesis. Public health cannot exercise spoken consent or paid audio. Authenticated ownership/consent/routing/replay verified locally on SQLite/PostgreSQL with providers/auth fixtures. Live paid production spoken request→STT→synthesis→replay and quota fallback remain unverified.

All11 new accents enabled:Indian,Scottish,Canadian,New Zealand,Chinese,Japanese,Filipino,Nigerian,Southern US,New York,Boston. Owner listening approval recorded; Clara remains unchanged and replacement isAUD39. Logs inspected after activation:no errors/tracebacks or unexpected accent fallback; tutor configuration unchanged. Receipts:docs/AUD-38-PRODUCTION-SMOKE.json,docs/AUD-38-CODE-DEPLOYMENT-SMOKE.json,docs/AUD-38-PRODUCTION-DEPLOYMENT.json.

Browser integrated real-media check1 passed; combined standard/integrated coverage138 passed. Extra no-saved-preference lesson/replay checks32 passed on SQLite/PostgreSQL. Frontend58 units/build/typecheck/lint passed; full backend1321 passed,7 backend-specific skips. No critical regression remains.
