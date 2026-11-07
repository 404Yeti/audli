#!/usr/bin/env bash
# Restore a pre-migration dump into a DISPOSABLE PostgreSQL 17 container and compare it with the fingerprint taken inside the dump's own snapshot.
#   scripts/verify_restore_pre_migration.sh ~/audli-backups/pre014-<stamp>
# Needs Docker Desktop running. Uses no production credentials and never opens a network connection to production. Everything happens inside one throwaway
# container (random name, random password, no published port) that is removed on exit, pass or fail. Output: PASS or FAIL with the reasons.
set -euo pipefail
case "$-" in *x*) echo 'refusing to run with xtrace enabled' >&2; exit 2;; esac
DIR="${1:?path to the pre014-<stamp> backup directory}"; DUMP="$DIR/audli-public.dump"; HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in audli-public.dump audli-public.dump.sha256 fingerprint.txt; do [[ -s "$DIR/$f" ]] || { echo "FAIL: missing $DIR/$f" >&2; exit 1; }; done
(cd "$DIR" && sha256sum -c --quiet audli-public.dump.sha256) || { echo 'FAIL: checksum mismatch, the backup file changed after it was taken' >&2; exit 1; }
echo 'checksum OK'
DOCKER=""; for c in docker docker.exe; do if command -v "$c" >/dev/null 2>&1 && timeout 30 "$c" info >/dev/null 2>&1; then DOCKER="$c"; break; fi; done
[[ -n "$DOCKER" ]] || { echo 'FAIL: no running Docker engine. Start Docker Desktop, wait for "Engine running", then retry.' >&2; exit 2; }
NAME="audli-restore-test-$$-$RANDOM"; PASS="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
cleanup() { "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true; echo "cleanup: container $NAME removed"; }; trap cleanup EXIT
"$DOCKER" run -d --name "$NAME" -e POSTGRES_PASSWORD="$PASS" postgres:17 >/dev/null
d() { "$DOCKER" exec -i "$NAME" "$@"; }
for _ in $(seq 1 90); do d pg_isready -U postgres -q 2>/dev/null && d psql -U postgres -qAt -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
d psql -U postgres -qAt -c 'select 1' >/dev/null || { echo 'FAIL: scratch server did not start' >&2; exit 1; }
echo "scratch server: $(d psql -U postgres -qAt -c 'show server_version')"
# The roles that the public schema's grants, policies and owners refer to (verified against production on 2026-11-07). No passwords, no login.
for role in anon authenticated service_role audli_api_runtime audli_claim_recovery_operator audli_listening_generator audli_listening_worker; do
  d psql -U postgres -qAt -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$role') THEN CREATE ROLE $role NOLOGIN; END IF; END \$\$" >/dev/null
done
# A fresh database already has an empty `public`; the dump recreates it, so drop the empty one (disposable DB) to get a clean, zero-error restore.
d psql -U postgres -qAt -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public' >/dev/null
# Restore (the archive goes in on stdin, so no file paths cross the Windows/WSL boundary).
set +e; d pg_restore -U postgres -d postgres --no-password < "$DUMP" 2> "$DIR/restore-errors.txt"; RC=$?; set -e
ERRS="$(grep -ci 'error' "$DIR/restore-errors.txt" || true)"
echo "pg_restore: exit=$RC error_lines=$ERRS"
# Fingerprint the restored copy with the same SQL and compare exactly.
d psql -U postgres -qAt -v ON_ERROR_STOP=1 < "$HERE/pre_migration_fingerprint.sql" > "$DIR/fingerprint-restored.txt"
FAIL=0
if diff -u "$DIR/fingerprint.txt" "$DIR/fingerprint-restored.txt" > "$DIR/fingerprint.diff"; then
  echo "fingerprint: IDENTICAL ($(grep -c . "$DIR/fingerprint.txt") lines: object counts and a content hash for every table)"
else FAIL=1; echo 'fingerprint: DIFFERENT (first differences below)'; head -20 "$DIR/fingerprint.diff"; fi
LEDGER="$(d psql -U postgres -qAt -c "select (select string_agg(version::text, ',' order by version) from listening_migrations) || ' / ' || (select string_agg(version::text, ',' order by version) from schema_migrations)")"
echo "ledgers restored (listening / main): $LEDGER"
(( RC == 0 && ERRS == 0 )) || FAIL=1
if (( FAIL == 0 )); then echo 'RESULT: PASS - the archive restored the complete public schema and every table matches the source snapshot.'
else echo "RESULT: FAIL - see $DIR/restore-errors.txt and $DIR/fingerprint.diff (these may contain row data: keep them private)"; exit 1; fi
