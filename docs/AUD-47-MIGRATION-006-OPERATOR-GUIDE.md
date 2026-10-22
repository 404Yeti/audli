# AUD-47 migration 006: operator guide (backup and isolated rehearsal)

For the owner. This guide covers **only** the read-only backup and the isolated rehearsal on a restored copy of production data.
It does **not** apply migration 006. Applying it (script `06`) needs a separate approval at a window you choose later.

Everything runs in **your own terminal**. Never paste a database password, connection string or backup content into chat; the scripts below read the
URL through a hidden prompt, keep it in a private file (mode 600) and redact it from all output.

Files under review: `docs/postgres-006.sql` sha256 `6b931920bacf728b854389463874d08009eb1003447008b15d5dda8976fcdb43`,
`docs/postgres-006-rollback.sql` sha256 `d9a3cd2ef796be60651abfc3a3fffd0068a6423d465f1d8cbf88a316130bf49c`.

## 1. What to open or install (once)
| Need | How to check / install |
|---|---|
| A Linux or WSL terminal in this repository (branch with `scripts/migration006`) | `cd` to the repo; `ls scripts/migration006` |
| PostgreSQL **client tools at least as new as your Supabase server** (`psql`, `pg_dump`, `pg_restore`) | Supabase dashboard → Project Settings → Infrastructure shows the Postgres major version (15 or 17). `psql --version` and `pg_dump --version` must show that major or newer. Ubuntu/WSL: `sudo apt install postgresql-client-17` (add the PGDG apt repository if the package is missing) |
| A disposable **rehearsal** server, PostgreSQL 17 on your own machine | Either Docker Desktop running (`docker info` works) **or** Node.js (`node --version`). The helper below starts it for you. No other software, no accounts |
| Your Supabase **connection string** | Dashboard → Connect → **Session pooler** (port 5432, user `postgres.<project-ref>`, works over IPv4), or **Direct connection** (IPv6 only unless the IPv4 add-on is enabled). **Do not use the Transaction pooler (port 6543); the scripts refuse it.** Replace `[YOUR-PASSWORD]` with your database password. Use `?sslmode=require` if shown |
| About 3× the database size in free disk | `01` prints the database size; it is small today |

Extensions and roles: none required. The schema (migrations 001–006) contains no `CREATE EXTENSION`, no policies and no references to Supabase's
`auth` schema; migration 006 uses only plain SQL and PL/pgSQL. The backup is `pg_dump --schema=public --no-owner --no-privileges`, so it restores on a vanilla PostgreSQL.
The rehearsal server must be the same major version as production or newer; `03` checks this and stops otherwise.

## 2. Checklist
"You" = you type the command and decide to continue. **Production is only ever read** in steps A2–A3 and C1; there is no production write anywhere in this guide.

| # | Step (run from the repository root; `S=scripts/migration006`) | Touches production? | You authorize | PASS looks like | If it fails |
|---|---|---|---|---|---|
| A0 | `$S/00-rehearsal-server.sh start` | No (local) | Starting a local test database | `STARTED. AUDLI_REHEARSAL_DATABASE_URL is stored …` | Read the message. No Docker and no Node → install one. Port busy → `--port 54340`. Stop and report if unresolved |
| A1 | `$S/00-set-production-url.sh` and paste the connection string at the hidden prompt | No (shape check only) | Storing the URL in `~/.config/audli/migration006.env` (mode 600) | `STORED.` plus `target: host=… db=postgres fingerprint: <12 hex>`. **Write the fingerprint down** | "not a complete URL" → copy the string again. "port 6543" → use the session pooler |
| A2 | `$S/01-preflight.sh` | **Reads** (server rejects writes) | Reading production | `PREFLIGHT PASS (production was only read)` and `run directory: <path>`. Set `RUN=<that path>` | Any `FAIL\|…` line: **stop, send me that line** (it contains no secrets). Common: client older than server → install a newer `postgresql-client`; network/auth error → check the string and that your IP is allowed |
| A3 | `$S/02-backup.sh --run-dir $RUN` | **Reads** one snapshot | Taking a backup of production data onto this machine | `BACKUP WRITTEN.` with dump bytes and sha256. The dump stays in `$RUN` (mode 600); do not share it | Report the error; delete nothing. Re-run in a new run directory (`01` first) |
| B1 | `$S/03-restore-verify.sh --run-dir $RUN` | No | — | `RESTORE VERIFIED into audli_rehearsal_…. The backup is restorable.` and `all N table counts … match the backup snapshot` | Any difference is printed and saved in `$RUN/restore-diff.txt`. **Stop and report; do not repair** |
| B2 | `$S/04-rehearse.sh --run-dir $RUN --app-smoke` | No | — | `REHEARSAL PASS: migration <s>s, post-checks, guard behavior, rollback and re-apply verified on restored production data.` Steps 1–7 each show `ok`/`PASS`, no `FAIL` | The failing check name is printed. **Stop and report; do not repair.** The restored copy may be discarded and the run repeated |
| B3 | `$S/05-postcheck.sh --run-dir $RUN --target rehearsal` | No | — | `POSTCHECK PASS on rehearsal. SQL rollback safe: SAFE.` | As B2 |
| C1 | `$S/06-apply-production.sh --run-dir $RUN --expected-sha256 6b931920bacf728b854389463874d08009eb1003447008b15d5dda8976fcdb43 --target-fingerprint <fingerprint from A1> --dry-run` | **Reads**; writes nothing | Reading production once more | `DRY RUN COMPLETE: every gate passed … Nothing was written.` | The refusal names the missing gate (stale evidence, wrong hash or fingerprint, failed manifest). Repeat the earlier step it names |
| — | Cleanup after the evidence is reviewed: `$S/00-rehearsal-server.sh stop` and keep or delete `$RUN` (it holds production data; keep it private) | No | — | `STOPPED.` | — |

What to send me afterwards (no secrets in any of it): the final PASS line of every step, and `$RUN/*.json` (stage manifests: results, hashes, timings), plus
`$RUN/restore-diff.txt` if it exists. I will not ask for the URL, the dump or any row contents.

The real apply (`06` with `--confirm "APPLY MIGRATION 006 TO PRODUCTION"`) is **not** part of this guide. It also needs a recent `01`–`04` run (evidence expires after 24 h)
and your separate approval. `07-rollback-production.sh` exists but is not used and is not needed for the rehearsal; the rehearsal already tests the rollback file on the copy.

## 2a. Where things live, isolation, and secure cleanup
* **Outside Git:** the credentials file (`~/.config/audli/migration006.env`, mode 600), the run directories (`~/.config/audli/migration006/<timestamp>/`, mode 700; dump and counts mode 600)
  and the rehearsal server's data all live under `~/.config/audli`. The scripts refuse a credentials file or run directory inside the repository, and `.gitignore` also covers `*.dump` and `migration006.env`.
  Nothing from this procedure is uploaded by CI or to Linear/GitHub; I only ever ask for redacted PASS lines and the non-sensitive manifest fields.
* **Rehearsal isolation:** the rehearsal server listens on `127.0.0.1` only (Docker publishes `127.0.0.1:PORT`), each run restores into a brand-new `audli_rehearsal_*` database,
  and the scripts refuse a rehearsal URL that is not loopback, looks like Supabase or has the same fingerprint as production. Every write session also passes an internal guard that refuses to address the production database.
* **Cleanup when the evidence has been reviewed** (the dump contains real learner data; treat it like production):
  1. `$S/00-rehearsal-server.sh stop` (Docker: removes the container; npm: stops the server).
  2. Delete the run directory securely: `shred -u $RUN/backup.dump 2>/dev/null; rm -rf $RUN` (on SSDs/journaling filesystems `shred` is best-effort; deleting the file is the main protection, so keep the folder private until then).
  3. Delete the rehearsal data: `rm -rf ~/.config/audli/migration006/rehearsal-server` (npm engine) — Docker needs nothing more.
  4. Remove the credentials: `shred -u ~/.config/audli/migration006.env 2>/dev/null; rm -f ~/.config/audli/migration006.env`, then, if you want to rotate it, change the database password in Supabase (Project Settings → Database) afterwards.
  5. Clear the terminal scrollback; the URL was never typed on a command line, so it is not in shell history.
  Keep only the stage manifests (`$RUN/*.json`; no secrets) if you want a record, e.g. copy them to a notes folder before step 2.

## 3. What each PASS proves
* **A2 preflight**: the live schema is exactly versions 1–5, no ledger yet, original status check and index, no long-running transactions or blocking locks. Baseline counts recorded.
* **A3 backup**: dump and row counts come from one consistent snapshot, so the next step compares exactly.
* **B1 restore**: the dump restores without errors into a brand-new database and every table count, lesson statuses, a checksum of all lesson rows and the schema versions equal the snapshot.
* **B2 rehearsal**: the exact `docs/postgres-006.sql` (hash shown) applies to the restored copy within the production timeouts; 16 post-checks (entitlement backfill, lesson statuses, constraints, indexes, row-level security, trigger); the guard trigger accepts and rejects the right updates (inside rolled-back transactions); application read endpoints work for every restored learner in off and observe mode; the shipped rollback restores the pre-006 shape and data; re-applying works.
* **B3**: the checks pass again and the rollback-safety verdict is printed (`SAFE` = no enforce-mode data exists).

## 4. When something is wrong
Scripts never repair, restore or roll back by themselves. On any `FAIL` or refusal: stop, copy the printed lines (they are redacted) and report. Production is unchanged by every step in this guide.
