# Private beta operating policy (AUD-96)

This is a proposed operating policy, not permission to merge, deploy, change Auth,
change production settings, spend provider credits or reopen admission. The owner
approves those actions separately for this release.

## Learning allowance

Keep `AUDLI_LESSON_LIMIT_MODE=enforce`. Set `AUDLI_LESSON_COMPLETED_LIMIT=10`
(default; accepted range 1–100). A lesson counts once it has canonical `completed`
status, using `completed_at` in the rolling 24-hour window. Exactly 24 hours after
completion it stops counting. Existing completed rows without a completion timestamp
use their recorded start time; no history is rewritten. Expired, abandoned, paused
and failed lessons do not count, including assessed exercises in unfinished lessons.
A lesson still open must be resumed or settled, not duplicated.

Configure `AUDLI_DEVELOPER_USER_ID` to the privately confirmed owner's Supabase Auth
UUID. It is optional and defaults to no exemption. Only server-verified Supabase
request identity receives the exemption; local identity, browser IDs, email, profile
fields and user-editable metadata cannot enable it. Do not commit the real UUID.

The exemption removes only learning quota. Existing 10-minute wall-clock deadlines,
3-minute closing grace, bounded wrap-up provider actions, audio validation, claims,
fencing, upload IDs, retry cap and provider budgets remain. Billable failures are
not refunded from provider spending controls. An expired old lesson may be followed
by a fresh one; its stale requests cannot consume the new lesson's grace counters.

No migrations or grants are required. Schema 006/008 ledger anchors and triggers
remain intact; the ledger continues binding provider wrap-up counters to the current
lesson. Learning eligibility comes from existing account-owned completed history.
Completion and creation retain their existing account locks/transactions/fencing.

## Five-account access preparation

The read-only inventory found nine confirmed, unbanned Auth accounts and signup
open. The private roster is outside Git, under the release operator's private
configuration directory, mode 0600. The owner marks exactly five `keep_enabled=true`
and one of those `developer_account=true`; all other rows must be explicitly decided.
Never publish roster emails, UUIDs, tokens or credentials in PRs, CI or Linear.

After separate approval, use supported Supabase Auth configuration/Admin controls:

1. Disable **Allow new users to sign up** globally; retain existing email/Google login
   and email confirmation. Do not disable an authentication provider as a substitute.
2. Reversibly ban the four unapproved accounts and revoke their sessions using
   supported Auth controls. Delete no account or application data. Confirm the
   five approved accounts remain enabled before reopening.
3. With an authorized pre-change test session for an account the owner approved to
   disable, check `/auth/v1/user` and a protected Audli endpoint before/after the ban.
   Both must reject it afterward; report statuses only. Audli revalidates the token
   through Auth on every request. A deterministic mocked regression covers this
   behavior, but does not prove hosted Auth enforcement.
4. Verify public signup is disabled and an approved account still signs in. If the
   old session remains usable, stop; do not reopen or introduce an ad hoc bypass.

The hosted disabled-session check is pending authorization and an appropriate test
session; no accounts have been disabled and no Auth configuration changed by this PR.

## Development and testing

Use isolated local data and mock providers by default. `AUDLI_CI_OFFLINE=1` with
`PYTHONPATH=ci:.` blocks external networking and rejects production/provider secrets.
Run targeted tests before pushing; required CI runs backend, disposable PostgreSQL,
rollback compatibility, frontend, browser and security checks. Existing Chromium
and WebKit fixtures provide repeatable recording and playback tests.

`AUDLI_PROVIDER=demo` with separate `AUDLI_DATA_DIR` supports local interface work;
it does not establish real STT or assessment quality. Explicit real-provider checks
need a scoped budget. Never run mocked tests with production credentials loaded.

## Routine releases

For an approved backward-compatible fix: targeted local tests → required green PR
CI → reviewed merge/deploy → revision/health check → brief smoke → done. A smoke test
may use mocks or cached/read-only behavior; provider-backed testing must be explicit.
Don't repeat architecture audits or disposable deployment rehearsals for unchanged
invariants. CI reruns remain required when the code/base changes or a check fails.

Maintenance is needed for incompatible cutovers or material incidents, not ordinary
UI fixes or compatible API patches. Use existing logs/metrics. Ordinary short-lived
transactions are expected, not incidents. Restore maintenance for actual exhaustion,
sustained pressure, stuck claims, fencing failures or material persistence errors.

| Change | Authorization and release treatment |
| --- | --- |
| Compatible UI/API fix, no access/spend/schema/ownership change | Routine workflow; future autonomous merge/deploy needs an explicit standing authorization from the owner |
| Schema migration, credential/permission change, account access expansion, provider-budget increase, claims/fencing protocol change, destructive action | Explicit scoped approval; relevant dry-run, backup/drain/cutover and recovery checks |

Rollback uses only schema-008-compatible releases. An allowance rollback may restore
the older restrictive learning quota; it does not downgrade the schema. Never deploy
pre-claims code or off mode on schema 008. Use the existing guard-aware paused/bridge
recovery procedure for ownership incidents; do not bypass fencing or drain checks.

## Short reopening sequence

Review this focused PR and privately confirm the five-account roster/developer UUID.
Separately approve merge/deployment and the exact Auth/settings changes above.
Keep maintenance during initial configuration verification; then approve one 3–5-minute
iPhone smoke: greeting (including Tap to play recovery), answer, assessment, progression.
If it passes, reopen to the approved five. Keep existing provider ceilings initially;
passage character limits are not an all-provider dollar hard cap. Wider access or
spending changes require a separate decision.
