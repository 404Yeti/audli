# Manual pre-migration backup and disposable restore test (Supabase Free: no managed backups)

The Free plan has no daily backups or point-in-time recovery, so before migration 014 we take our own logical backup with `pg_dump` and prove it restores. This procedure is **separate from migration execution**: neither script applies, or references, any migration, and the backup is read-only.

## Scope: what the backup does and does not cover
| Covered | NOT covered (outside this backup) |
|---|---|
| The whole `public` schema of the production database: all 1.x and `listening_*` tables, their data, indexes, constraints, triggers, policies, functions, **table/schema grants (ACLs)** and both ledgers (`schema_migrations`, `listening_migrations`) | **Supabase Auth users** (`auth` schema: accounts, passwords, sessions, MFA) and Auth configuration (providers, redirect URLs, templates, JWT settings) |
| `roles-and-grants.txt`: the role names, attributes, memberships, default ACLs and listening-table grants to recreate | Role **passwords** and the roles themselves (Supabase does not allow dumping them; recreate by hand, then run `docs/postgres-listening-api-role.sql`) |
| | The `storage` and `vault` schemas, Edge Functions, project settings, API keys |
| | **R2 audio objects** (lesson audio, generated audio): migration 014 never reads, writes or deletes them |
As of 2026-11-07 the `public` schema is 46 tables, ~60 MB, with no foreign keys to other schemas, no dependency on any extension, and references exactly these roles: `anon`, `authenticated`, `service_role`, `audli_api_runtime`, `audli_claim_recovery_operator`, `audli_listening_generator`, `audli_listening_worker` (the restore test creates all seven; a new one would show up as a restore error). A restore into a *new* Supabase project would also need the Auth users restored or re-linked separately. Migration 014 changes none of this; the backup is a safety net for the `public` schema.

## How the checks work
* **Consistent snapshot.** The backup opens one repeatable-read, read-only transaction, exports its snapshot, computes a *fingerprint* inside it and runs `pg_dump --snapshot` on the same snapshot. The fingerprint is: object counts (tables, columns, indexes, constraints, triggers, policies, functions, effective table grants) and, for every table, a row count and an MD5 of all rows in a fixed order under UTC. Because the dump and the fingerprint see the same instant, **live writes during the backup cannot make the comparison fail falsely** (tested: ~7,000 rows written during a backup, snapshot recorded 2,600, restore matched exactly).
* **Restore test.** A throwaway `postgres:17` container (random name and password, no published port) gets the seven roles, the archive on stdin, and the same fingerprint SQL; the two fingerprints must be identical, `pg_restore` must report zero errors, and the container is removed whatever the result.
* **Integrity.** SHA-256 recorded and re-verified; `pg_restore --list` parses; `pg_restore --file=/dev/null` reads the whole archive.

## Run it on Windows (Docker Desktop + WSL Ubuntu terminal)
Prerequisites (one time): Docker Desktop running ("Engine running") with **Settings > Resources > WSL integration** enabled for your Ubuntu distro; in Ubuntu `pg_dump --version` shows 17 or newer (`sudo apt install postgresql-client-17` if not). Get the **Session Pooler** string from the Supabase dashboard (Connect > Session pooler: host `aws-0-<region>.pooler.supabase.com`, **port 5432**, user `postgres.fopyknjekizcgrazisnq`). Port 6543 is the transaction pooler and is refused.

Open the **Ubuntu** terminal (not PowerShell; the scripts are bash):
```bash
cd /mnt/c/Users/Lenovo/projects/audli
git fetch origin && git checkout main && git pull        # after PR #156 is merged
read -rsp 'Session Pooler URL (input hidden): ' AUDLI_OWNER_URL; echo; export AUDLI_OWNER_URL
bash scripts/backup_pre_migration.sh                      # writes ~/audli-backups/pre014-<UTC stamp>/
unset AUDLI_OWNER_URL                                     # the backup is done; the restore test needs no credentials
bash scripts/verify_restore_pre_migration.sh "$(ls -dt ~/audli-backups/pre014-* | head -1)"
```
Type or paste the URL only at the hidden prompt; it is never echoed, logged, put on a command line or written to disk. If you must set it in a script, keep it out of git (`.env`, `*.dump`, `audli-backups/` and `pre014-*/` are ignored).

### Expected output: backup (values differ; what matters is the shape)
```
connection: kind=session-pooler host=aws-0-ap-northeast-1.pooler.supabase.com port=5432 user=postgres.fopyknjekizcgrazisnq (password not shown)
identity: current_user=postgres db=postgres listening_owner=postgres in_recovery=false server_version_num=170011
pg_dump major=17 server major=17
ledger: listening=[9,10,11,12] main=[1,2,3,4,5,6,7,8] library_table=absent
backup OK: /home/<you>/audli-backups/pre014-<stamp>  (<bytes> bytes, <n> TOC entries, archive parsed end to end, sha256 recorded)
```
It must say `listening=[9,10,11,12]` and `library_table=absent` (otherwise it prints a note and you stop and tell me). Any `stopping` line (wrong port, wrong project, not the owner, replica, old client) means nothing was written; fix and rerun.

### Expected output: restore test
```
checksum OK
scratch server: 17.x
pg_restore: exit=0 error_lines=0
fingerprint: IDENTICAL (<n> lines: object counts and a content hash for every table)
ledgers restored (listening / main): 9,10,11,12 / 1,2,3,4,5,6,7,8
RESULT: PASS - the archive restored the complete public schema and every table matches the source snapshot.
cleanup: container audli-restore-test-... removed
```
**PASS** is the only success. **FAIL** prints why (`restore-errors.txt`, `fingerprint.diff` in the backup folder; both may hold row data, keep them private). Only a printed `RESULT: PASS` counts as a tested restore.

### Cleanup
* The scratch container removes itself. Check with `docker ps -a | grep audli-restore-test` (nothing should be listed); if one remains: `docker rm -f <name>`.
* The backup folder holds learner data. Keep it private (directory mode 700). Keep it until migration 014 has been verified and the verification window has passed, then securely delete it (`rm -rf ~/audli-backups/pre014-<stamp>`).
* Nothing in the repository changes; `git status` stays clean.

## Restoring for real (disaster procedure)
Only if `public` data is damaged; migration 014 itself is rolled back with its own SQL (runbook section 6), not by restore.
1. Stop writers (API, worker, generator) or enable maintenance mode.
2. Restore into a NEW empty database first and compare fingerprints as above; only then decide whether to repoint the app or copy tables back. Immutability triggers on published listening versions block updates over live rows, so restore into an empty target.
3. Recreate roles and grants from `roles-and-grants.txt` (set passwords yourself), run `docs/postgres-listening-api-role.sql`, then check `/api/health` and a signed-in `/listening`. Auth users must be present for the `users` rows to be usable.
The dump is a point-in-time snapshot: anything written after it is lost, so take it immediately before applying 014.

## Evidence status (2026-11-07)
Run and passing: both scripts end to end on a real PostgreSQL 17.11 stand-in built from this repository's migrations 009-014 (roles, seeded lessons) through Docker Desktop's engine in WSL: backup OK, restore PASS with an identical fingerprint and zero restore errors; a deliberately corrupted fingerprint produced FAIL; a backup taken during live writes still restored to PASS; the credential/safety properties are pinned by `tests/test_backup_scripts.py`. **Not yet run:** the backup against the real production Session Pooler (needs the operator's URL; that run is the operator's) and the restore test on that production backup. No production restore has been tested.
