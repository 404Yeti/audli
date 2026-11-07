# Manual pre-migration backup (Supabase Free: no managed backups)

Why: the Free plan has no daily backups or point-in-time recovery, so before migration 014 we take our own logical backup with `pg_dump` and prove it restores. Scope: the whole `public` schema (46 tables, ~60 MB, no foreign keys to other schemas as of 2026-11-07), which includes `users`, the 1.x tables, all `listening_*` tables and both ledgers (`schema_migrations`, `listening_migrations`). Not covered: the `auth`, `storage` and `vault` schemas (Supabase-managed), the R2 audio objects (014 never touches them), and role passwords (Supabase does not allow dumping them; `roles-and-grants.txt` records the roles and grants to recreate).

## 1. Prerequisites (operator)
* Session Pooler URL for the owner role: host `aws-0-<region>.pooler.supabase.com`, **port 5432**, user `postgres.<project-ref>`, database `postgres`. Not port 6543 (transaction pooler; the script refuses it). The direct host `db.<ref>.supabase.co:5432` also works if you have IPv6.
* PostgreSQL client 17 or newer (server is 17.11; the script refuses an older `pg_dump`). `pg_dump --version` on the WSL machine was 17.11 on 2026-11-07.
* Disk: ~100 MB, outside the repository (`*.dump` is also git-ignored). The backup contains learner data: private, mode 700/600, delete after the verification window.
* For the restore test: Docker Desktop running (it pulls `postgres:17`, creates one throwaway container bound to 127.0.0.1 and removes it).

## 2. Take the backup
Type the URL yourself into your shell (leading space or `read -s` keeps it out of history); do not paste it into chat:
```bash
read -rs AUDLI_OWNER_URL && export AUDLI_OWNER_URL
scripts/backup_pre_migration.sh            # writes ~/audli-backups/pre014-<UTC stamp>/
```
It reads only. It prints the connection kind, host, port and user (never the password), the current user and table owner, server and client versions, the two ledgers, and then writes: `audli-public.dump` (custom format), `audli-public.dump.sha256`, `row-counts-before/after.tsv`, `roles-and-grants.txt`, `toc.txt`, `MANIFEST.txt`. It stops if: port 6543, wrong project ref, not the table owner, a replica, or `pg_dump` older than the server. Integrity checks: the SHA-256 is recorded and re-verified, `pg_restore --list` parses, and `pg_restore --file=/dev/null` reads the archive end to end.

## 3. Prove it restores (disposable instance)
```bash
scripts/verify_restore_pre_migration.sh ~/audli-backups/pre014-<stamp>
```
It re-checks the checksum, starts a throwaway `postgres:17` container with a random password, creates the roles named in the grants, restores the dump, compares every table's row count with the counts taken before and after the dump (ledgers must match exactly; other tables must fall between the two, since the app is live), prints the ledgers and the API role's privileges, and removes the container. **Only a printed `RESTORE TEST PASSED` counts as a tested restore.** Until it has been run, the restore is untested.

## 4. Restoring for real (disaster procedure)
Only if data is damaged; migration 014 itself is rolled back with its own SQL (runbook section 6), not by restore.
1. Stop writers: scale the API, worker and generator to zero (or maintenance mode).
2. Restore into a NEW empty database first (a new Supabase project or a scratch server), verify as in section 3, and only then decide whether to repoint the application or copy tables back.
3. In-place restore of selected tables from the archive: `pg_restore --data-only --table=<name> --dbname=<target> audli-public.dump` (disable triggers/FK order as needed; listening immutability triggers block updates to published versions, so restore into an empty target rather than over live rows).
4. Recreate roles and grants from `roles-and-grants.txt` (passwords must be set by the operator), run `docs/postgres-listening-api-role.sql`, then check `/api/health` and a signed-in `/listening`.
Known limits: the dump is a point-in-time snapshot (anything after it is lost; take it immediately before applying 014), it does not include Auth users (a restore into a new project needs the Auth users restored or re-linked separately), and restore time is minutes at this size.

## 5. Evidence status
Verified in this repository: script syntax, the credential handling (refuses 6543, a wrong project ref and xtrace; a fake password never appeared in output), client/server version match (17.11). **Not yet run:** the live backup (needs the operator's URL) and the restore test (needs Docker running). No restore has been tested.
