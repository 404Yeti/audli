# AUD-59 disabled-homework regression

The preflight at `9710dd3` found that an invisible homework component still fetched
`/api/homework`. Supabase middleware therefore performed one additional
`/auth/v1/user` verification per mount, even when the endpoint returned disabled.

The existing public `/api/auth/config` bootstrap now also returns the server's
boolean `homework_enabled`. AuthGate already fetches this response; no capability
endpoint or additional request is introduced. The existing account context carries
only an explicit-true opt-in. Missing, null, malformed or unresolved capability
values default off. A failed bootstrap retains the existing connection-retry flow.

`ListeningHomework` mounts its owned loader, state and voice hooks only for an
explicit enabled capability. Disabled/unknown states make **zero homework API
requests** and therefore zero homework-related Supabase verifications, YouTube,
STT or TTS calls. Ordinary authenticated lesson/Plan/Review traffic remains.
Protected homework endpoints still require verified ownership; manually calling
one still authenticates. The client capability is a presentation gate, never an
authorization decision or an identity cache.

The enabled child is keyed by account scope and unmounts on sign-out/capability
disable. Existing request abort/revision and audio lifetime guards discard late
responses. No capability is persisted in browser storage. Enabled homework keeps
its existing post-completion invitation, spoken decision and confirmation flow.

## Evidence

An instrumented offline ASGI probe used the real API routes and a mocked Supabase
HTTP transport. It compared the old unconditional loader with the existing
bootstrap plus conditional loader. Browser tests independently exercise the
actual UI gate; the ASGI probe is not a live Supabase or YouTube test.

| Disabled loader path | Homework API requests | Outbound auth verifications |
| --- | ---: | ---: |
| Before (`9710dd3`) | 1 | 1 |
| After bootstrap gate | 0 | 0 |

Backend regression run: **188 passed** across homework, auth, lessons and planner.
The final instrumented bootstrap assertions also passed separately (**3 passed**).
Browser coverage includes enabled, disabled, missing, null, malformed and failed
capabilities, zero requests in Plan/completion, Supabase-mode sign-out, and delayed
homework response isolation across account switching. Final browser/hosted CI
results are recorded in PR #42; prior-head green checks are not substituted.

Local final capability/auth browser suite: **12 passed**. All **17 existing
homework browser cases** also passed in the preceding combined run. That combined
run had one new enabled request-count assertion fail because development Strict
Mode restarted the existing loader; the corrected test checks that enabled
loading works and does not refetch the bootstrap on Plan navigation. An earlier
run exposed an invalid Review fixture, which was corrected. Final lint, TypeScript,
unit tests, lint-rule coverage and production build passed. No failed/intermediate
run is described as clean all-pass coverage.

## Release boundaries

Homework remains disabled by default; daily discovery admission remains zero.
Production configuration, credentials and database state are unchanged. No new
migration is introduced. Schema 004 remains supported without optional homework
tables. **Do not run migration 005 for the disabled deployment.** The generic
migration runner would apply 005 regardless of the flag, and the previous main
binary would reject its advanced ledger during rollback.

Both exact AUD-59 branch preview-disable entries remain unchanged. An eventual
owner-approved merge still triggers existing Render main/commit auto-deployment
and Vercel main production integrations; this fix adds no deployment gate.

Protected merge is conditional GO once all eight required checks pass on the new
published revision and the owner approves the automatic deployment implications.
Production activation stays **NO-GO** pending authorized live YouTube discovery,
real playback/suitability and real-device spoken acceptance. No merge, deploy,
production migration, enabling or credential/configuration change is authorized
by this fix.
