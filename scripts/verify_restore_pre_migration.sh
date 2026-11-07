#!/usr/bin/env bash
# Restore a pre-migration dump into a DISPOSABLE local PostgreSQL 17 container and compare it with the manifest. Touches nothing but the container.
#   scripts/verify_restore_pre_migration.sh ~/audli-backups/pre014-<stamp>        (Docker must be running; no production credentials are used)
set -euo pipefail
DIR="${1:?path to the pre014-<stamp> backup directory}"
DUMP="$DIR/audli-public.dump"
[[ -f "$DUMP" ]] || { echo "no $DUMP" >&2; exit 2; }
(cd "$DIR" && sha256sum -c --quiet audli-public.dump.sha256) || { echo 'checksum mismatch: the backup file changed' >&2; exit 1; }
DOCKER="$(command -v docker || command -v docker.exe || true)"; [[ -n "$DOCKER" ]] || { echo 'docker not found' >&2; exit 2; }
"$DOCKER" info >/dev/null 2>&1 || { echo 'the Docker engine is not running; start Docker Desktop' >&2; exit 2; }
NAME="audli-restore-test-$$"; PASS="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"   # throwaway password, container-only
cleanup() { "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true; }; trap cleanup EXIT
"$DOCKER" run -d --name "$NAME" -e POSTGRES_PASSWORD="$PASS" -p 127.0.0.1::5432 postgres:17 >/dev/null
PORT="$("$DOCKER" port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"
export PGHOST=127.0.0.1 PGPORT="$PORT" PGUSER=postgres PGPASSWORD="$PASS" PGDATABASE=postgres
for _ in $(seq 1 60); do pg_isready -q && break; sleep 1; done; pg_isready -q || { echo 'scratch server did not start' >&2; exit 1; }
q() { psql -X -qAt -v ON_ERROR_STOP=1 -c "$1"; }
# Recreate the roles the dump's grants refer to (names from the manifest's roles file; no passwords).
for role in anon authenticated service_role audli_api_runtime audli_listening_worker audli_listening_generator audli_claim_recovery_operator; do
  q "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$role') THEN CREATE ROLE $role NOLOGIN; END IF; END \$\$"
done
q 'CREATE SCHEMA IF NOT EXISTS extensions'; q 'CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions'; q 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions'
set +e; pg_restore --dbname=postgres --no-password "$DUMP" 2> "$DIR/restore-errors.txt"; RC=$?; set -e
ERRS="$(grep -c 'error' "$DIR/restore-errors.txt" || true)"
echo "pg_restore exit=$RC, error lines=$ERRS (details: $DIR/restore-errors.txt)"
FAIL=0
COUNTS_SQL="SELECT table_name || E'\t' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1"
q "$COUNTS_SQL" > "$DIR/row-counts-restored.tsv"
python3 - "$DIR" <<'PY' || FAIL=1
import sys, pathlib
d = pathlib.Path(sys.argv[1]); rd = lambda n: dict(l.split('\t') for l in (d / n).read_text().split('\n') if l)
b, a, r = rd('row-counts-before.tsv'), rd('row-counts-after.tsv'), rd('row-counts-restored.tsv')
bad = 0
for t in sorted(set(b) | set(r)):
    lo, hi, got = int(b.get(t, 0)), int(a.get(t, 0)), int(r.get(t, -1))
    exact = t in ('listening_migrations', 'schema_migrations')
    ok = (lo == hi == got) if exact else (min(lo, hi) <= got <= max(lo, hi))
    if not ok: bad += 1; print(f'  MISMATCH {t}: before={lo} after={hi} restored={got}')
print(f'tables compared: {len(set(b) | set(r))}, mismatches: {bad}'); sys.exit(1 if bad else 0)
PY
echo "ledger listening=[$(q 'SELECT string_agg(version::text, chr(44) ORDER BY version) FROM listening_migrations')] main=[$(q 'SELECT string_agg(version::text, chr(44) ORDER BY version) FROM schema_migrations')] (expect 9,10,11,12 / 1..8)"
echo "api role privileges on listening_lessons: $(q "SELECT coalesce(string_agg(privilege_type, chr(44) ORDER BY privilege_type), 'none') FROM information_schema.role_table_grants WHERE grantee='audli_api_runtime' AND table_name='listening_lessons'") (expect INSERT,SELECT,UPDATE)"
(( RC == 0 && ERRS == 0 && FAIL == 0 )) && echo 'RESTORE TEST PASSED (disposable container removed)' || { echo 'RESTORE TEST DID NOT PASS: read restore-errors.txt and the mismatches above'; exit 1; }
