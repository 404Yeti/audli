# Migration 006 toolchain (AUD-47)

Repeatable, reviewed scripts for applying `docs/postgres-006.sql` (durable daily-lesson ledger) to production safely.
**Nothing here runs by itself and nothing mutates production except `06-apply-production.sh` and `07-rollback-production.sh`,
each behind several explicit gates.** Production changes need the owner's separate approval; these scripts do not grant it.

| Script | Category | Touches production? | What it does |
|---|---|---|---|
| `01-preflight.sh` | READ-ONLY | reads (server refuses writes) | schema must be exactly 1–5, ledger absent, original status check/index, no long transactions/locks; baseline counts; fingerprints the migration file |
| `02-backup.sh` | BACKUP | reads one consistent snapshot | `pg_dump -Fc` of schema `public` + counts from the **same snapshot**; size, sha256, table of contents |
| `03-restore-verify.sh` | ISOLATED | no | restores the dump into a **new** database on the rehearsal server and compares every table count, lesson statuses, lesson-row checksum and schema versions with the snapshot |
| `04-rehearse.sh` | ISOLATED | no | on the restored copy: preflight → apply 006 (timed, production timeouts) → post-checks → guard-trigger behavior → optional `--app-smoke` → rollback → re-apply → post-checks |
| `05-postcheck.sh` | READ-ONLY | reads | the post-migration checks + the **rollback-safety verdict**; works on `--target rehearsal` or `--target production` |
| `06-apply-production.sh` | **PRODUCTION-MUTATING** | **writes** | applies 006 only when every gate below passes; never rolls back or restores by itself |
| `07-rollback-production.sh` | **PRODUCTION-MUTATING** | **writes** | manual only; applies `docs/postgres-006-rollback.sql` only if the live verdict is SAFE |
| `selftest.sh` | local only | no | runs the whole toolchain + every refusal path against a local PostgreSQL stand-in; `make_fixture.py` builds the production-shaped data |

## Operator guide
Step-by-step instructions for the owner (what to install, which steps need approval, what PASS looks like, what to do on failure):
`docs/AUD-47-MIGRATION-006-OPERATOR-GUIDE.md`.

## Setup (credentials are never printed or stored)
* `00-set-production-url.sh` stores the production URL with a hidden prompt; `00-rehearsal-server.sh start` starts a disposable local PostgreSQL 17 (Docker, or npm `embedded-postgres`) and stores its URL.
* Supabase connection: the **direct** connection (IPv6 unless the IPv4 add-on is enabled) or the **session pooler** (port 5432, user `postgres.<project-ref>`, works over IPv4). The **transaction pooler (port 6543) is refused**: it cannot hold the exported snapshot or session settings.
* Session settings (`read only`, timeouts) are sent as `SET` commands, not as the libpq `options` startup parameter, which Supabase's poolers reject.
* Client tools (`psql`, `pg_dump`, `pg_restore`) must be **at least the server's major version** (`02` stops otherwise).
* Put the two URLs in a file with mode `600` (default `~/.config/audli/migration006.env`, or `--env-file`), or export them:
  ```sh
  export AUDLI_PROD_DATABASE_URL='postgresql://…production session-pooler URL…'
  export AUDLI_REHEARSAL_DATABASE_URL='postgresql://user:pw@127.0.0.1:5432/postgres'   # a disposable local server you own
  ```
  The URLs travel only through libpq environment variables; output is passed through a redactor (password, user, host, IPs). Scripts print a
  masked host, the database name and a 12-character **fingerprint** of host/port/database.
* The rehearsal server must be loopback (or pass `--allow-remote-rehearsal` for a disposable remote one), must not look like Supabase and must not be the
  production database. Each run creates a fresh `audli_rehearsal_*` database; nothing existing is touched.

## Run order
```sh
S=scripts/migration006
$S/01-preflight.sh                                # prints the run directory, the target fingerprint and the migration sha256
RUN=~/.config/audli/migration006/<printed timestamp>
$S/02-backup.sh            --run-dir $RUN
$S/03-restore-verify.sh    --run-dir $RUN         # proves the backup restores, exactly
$S/04-rehearse.sh          --run-dir $RUN --app-smoke
$S/05-postcheck.sh         --run-dir $RUN --target rehearsal
# --- owner approval needed from here; the dry run writes nothing ---
$S/06-apply-production.sh  --run-dir $RUN --expected-sha256 <sha256 reviewed> --target-fingerprint <fingerprint from 01> --dry-run
# the real apply additionally needs:   --confirm "APPLY MIGRATION 006 TO PRODUCTION"
```
Gates for `06` (all must hold, checked in this order, before any write): the fingerprint equals the database being addressed; the file on disk hashes to
`--expected-sha256` (the hash you reviewed; `01` records it, and `04` rehearsed exactly that file); the exact confirmation phrase; preflight, backup,
restore verification and rehearsal manifests exist, **PASSED**, belong to this file and this database, and are younger than `--max-age-hours` (24);
a fresh read-only preflight passes. The file is one transaction (`lock_timeout 5s`, `statement_timeout 30s`): any failure leaves production unchanged.
After a successful apply it runs `05-postcheck.sh --target production` and prints the rollback verdict. **No script ever restores or rolls back production automatically.**

## What the post-checks assert (`sql/postcheck.sql`)
versions exactly 1–6 with 6 recorded once · ledger columns exact · one ledger row per learner with lessons, and (strict phase) each equals that learner's
latest lesson start, +24 h, nothing voided · status check allows `expired` · open-lesson index is `active|paused` only · ledger constraints present, **no FK to lesson
rows** · row-level security on, `anon`/`authenticated`/PUBLIC have no access · guard trigger present, enabled, BEFORE UPDATE row · and `historical_data_unchanged`
(rehearsal: every count, status and the lesson-row checksum equal the dump snapshot; production: no table has fewer rows than immediately before the apply).
`--phase observe` relaxes only what legitimately drifts once the ledger keeps recording new starts.

## Is the SQL rollback still safe? (`sql/rollback_safety.sql`, printed by every `05`, re-checked by `07`)
`docs/postgres-006-rollback.sql` drops the ledger and the `expired` status. It is **SAFE only if** no lesson has status `expired`, no ledger row records a void,
a grace slot or a first-speech failure (those are written only by enforce mode), and the schema version is 6. Rows in the ledger that merely recorded observe-mode starts are harmless to drop.
* `SAFE` → rolling back loses only 24-hour window state; `07` is available with explicit approval.
* `UNSAFE` → enforce mode has written data. **Do not run the SQL rollback**; set `AUDLI_LESSON_LIMIT_MODE=off` (instant, no schema change).
Decision when a post-check fails right after the apply: read the verdict line first; stay in mode `off` (the application never touches the ledger in that mode), investigate on the restored copy, and ask the owner before any rollback.

## Evidence recorded in this repository's history
`selftest.sh` (37 checks, trust-auth and SCRAM-password PostgreSQL 17.10): preflight → snapshot backup → restore → rehearsal (apply 0.08 s on 176 lessons, 60 learners (51 with lessons), 16 post-checks, guard
behavior, app smoke for all learners in off/observe, rollback, re-apply) → every refusal of `06`/`07` → dry run → real apply → SAFE/UNSAFE verdicts → real rollback → no credential in any output.
**Not yet done:** a rehearsal on a restored dump of the real production database. That is step 03–04 above and needs the owner's credentials; until it passes, production data has not been exercised.
