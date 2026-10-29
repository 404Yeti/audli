# AUD-94 exact role privilege matrix

Proposed application schema privileges; no production changes. S/I/U/D = SELECT/INSERT/UPDATE/DELETE. All listed tables have RLS; backend policies are trusted-server policies, not end-user authorization. Browser/SQL API roles receive no new access. Existing canonical service_role privileges are reviewed separately below.

| Table | Backend API | Recovery operator | Browser roles / PUBLIC |
| --- | --- | --- | --- |
| `users` | S/I/U/D | S | No new access |
| `audio_assets` | S/I/U/D | S | No new access |
| `learner_profiles` | S/I/U/D | S; U(user_id) for profile lock only | No new access |
| `learner_roadmaps` | S/I/U/D | S | No new access |
| `lesson_entitlements` | S/I/U/D | S | No new access |
| `lesson_lifecycles` | S/I/U/D | S | No new access |
| `training_sessions` | S/I/U/D | S | No new access |
| `exercises` | S/I/U/D | S | No new access |
| `homework_assignments` | S/I/U/D | S | No new access |
| `attempts` | S/I/U/D | S | No new access |
| `coach_audio` | S/I/U/D | S | No new access |
| `conversation_followups` | S/I/U/D | S | No new access |
| `homework_reflections` | S/I/U/D | S | No new access |
| `lesson_exercises` | S/I/U/D | S | No new access |
| `adaptation_events` | S/I/U/D | S | No new access |
| `conversation_turns` | S/I/U/D | S | No new access |
| `conversations` | S/I/U/D | S | No new access |
| `evaluation_results` | S/I/U/D | S | No new access |
| `review_improvements` | S/I/U/D | S | No new access |
| `turn_assessments` | S/I/U/D | S | No new access |
| `assessment_evidence` | S/I/U/D | S | No new access |
| `schema_migrations` | S, role-specific SELECT policy | None required by current tool | No new access |
| `accent_control` | S; U(budget_day,characters) only | None | No new access |
| `accent_reservations` | S/I/U/D | None | No new access |
| `homework_discovery_budget` | S/I/U | None | No new access |
| `listening_content` | S/I/U | None | No new access |
| `poc_account_slots` | S/I/U/D | S; U(active,fence) | None |
| `poc_upload_operations` | S/I/U/D | None | None |
| `poc_stage_operations` | S/I/U/D | S; U(status) | None |
| `poc_recovery_audit` | S/D (account erasure); no I/U | S/I; no U/D | None |

No new sequences exist. Future sequence introduction must fail review pending explicit USAGE/SELECT requirements. Neither runtime nor recovery role gets table ownership, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN, schema CREATE, BYPASSRLS or role membership management. Both need public-schema USAGE. Migration administrator owns DDL and schema ledger writes, outside request credentials.

Backend EXECUTE is granted only on the five reviewed SECURITY INVOKER functions: poc_stage_read, poc_stage_claim, poc_stage_apply, poc_claim_guard and poc_checkpoint_parent_guard with their exact migration signatures. No PUBLIC execution and no SECURITY DEFINER escalation. Recovery is a separate pre-provisioned NOLOGIN role selected only by an authorized operator; backend must not inherit it.

## Managed Auth and Storage (separate permission gate)

| Object | Conditional backend requirement when account deletion enabled |
| --- | --- |
| auth schema | USAGE |
| auth.users | S/D, U(id) for row locking |
| auth.sessions | S/D |
| auth.refresh_tokens | S/D |
| auth.flow_state | S/D |
| auth.scim_users | S/D |
| storage schema / storage.buckets | USAGE / S to fail closed if buckets exist |

RLS policies for these privileges must be reviewed against the actual managed schema; table grants alone are insufficient. Local tests provide synthetic explicit policies. Identity/MFA/OAuth/WebAuthn/session-child deletion uses the catalog-verified Auth FK cascades; no broad direct grants on every Auth child. Storage physical object deletion is not implemented; nonempty buckets cause refusal, not metadata-only erasure.

## Existing service_role non-DML privileges: separate proposal

Previously observed service_role has TRUNCATE on25 canonical public application tables although no SELECT/INSERT. RLS does not protect TRUNCATE. Do not confuse this with newly denied claims-table access or apply a global public-schema revocation.

After confirming current memberships, ownership, migrations, maintenance and all service-role consumers, propose an explicitly enumerated, transactional REVOKE of TRUNCATE (and unused REFERENCES/TRIGGER/MAINTAIN) on application-owned canonical tables from service_role and any other client grantees; adjust the corresponding creator-specific default ACLs for application tables. Preserve required Auth/Storage/extension/platform grants. Rehearse on the actual-role replica and verify legitimate jobs before approval. No production changes were executed.

Server column grants intentionally prevent runtime `configure()` from changing accent enabled/daily/concurrent/revision settings. Such operator changes require a separate operator role; no HTTP route is added.
