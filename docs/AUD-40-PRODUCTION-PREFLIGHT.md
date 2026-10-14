# AUD-40 production preflight

The final release review stopped because production revision
`0a2f515ee2d987bd3304af5d768df6f6d5117666` accepts only schema 001–003 and cannot
restart after 004. Production was verified on ledger 001–003; no migration or
deployment proceeded. The three planner behavior fixes remain a separate local
implementation and are not included in this bridge.

The focused compatibility candidate is based on that exact production revision.
It accepts only schema 003/004 ledgers and preserves optional planner lesson
metadata without activating planning. See [bridge certification and controlled
transition](AUD-40-SCHEMA-BRIDGE.md) for verification and rollback details.

Local checks passed: full SQLite/PostgreSQL backend **1332 passed, 16 skipped**;
schema-004 selection **369 passed, 15 skipped**; final focused startup/lifespan
checks **10 passed, 9 skipped**; browser **137 passed, 1 native-media-fixture skip**;
frontend units, lint, TypeScript, production build and diff whitespace checks.
All provider/identity calls are mocked. No paid provider calls occurred.

This local certification does not complete production preflight: deployment of
the bridge on 003, production health, verified backup restoration, migration
compatibility on restored data and post-004 bridge health remain mandatory future
gates. The full planner release needs a separate focused PR and fresh regressions.
AUD-40 remains In Progress; no production release is claimed.
