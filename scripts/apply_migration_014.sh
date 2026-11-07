#!/usr/bin/env bash
# Fail-closed, one-shot application of migration 014 (listening_library_entries). Applies ONLY docs/postgres-014.sql, exactly as approved.
#
#   read -rsp 'Owner Session Pooler URL (port 5432): ' AUDLI_OWNER_URL; echo; export AUDLI_OWNER_URL
#   bash scripts/apply_migration_014.sh ~/audli-backups/pre014-<stamp>
#
# Every check below must pass BEFORE the SQL runs; any surprise stops the script with exit 1 and nothing has changed. The SQL file is a single transaction
# (it commits fully or leaves nothing). There is NO retry and NO automatic rollback: if verification after the apply fails, the script says so, prints the
# documented rollback (docs/LISTENING-LIBRARY-MIGRATION-014.md section 8, case A) and leaves the decision to you.
# The URL is parsed into libpq environment variables inside this process; it is never printed, written to a file or put on a command line. xtrace is refused.
set -euo pipefail
case "$-" in *x*) echo 'refusing to run with xtrace enabled (it would print the credentials)' >&2; exit 2;; esac
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; SQL="$HERE/../docs/postgres-014.sql"
APPROVED_SHA256=451ca00a415119f4534c8c5b9a19aba7f66fcc67c6a9512288ab58d139cd0703
PROJECT_REF=fopyknjekizcgrazisnq
BACKUP="${1:?usage: apply_migration_014.sh <verified pre014-<stamp> backup directory>}"
: "${AUDLI_OWNER_URL:?set AUDLI_OWNER_URL to the owner (postgres) Session Pooler URL, port 5432}"
# Test hook for the automated tests only: a LOOPBACK throwaway server may skip the Supabase host/project/port identity checks. It can never reach production
# because the parser below refuses the flag for any non-loopback host.
DISPOSABLE="${AUDLI_APPLY_DISPOSABLE_TEST:-}"

STAGE='starting'; APPLIED=no
stop() { echo "STOPPED at '$STAGE': $*" >&2; exit 1; }
on_exit() {
  local rc=$?; (( rc == 0 )) && return 0
  if [[ "$APPLIED" == yes ]]; then
    echo "!! The migration was applied but verification did not pass (stage: $STAGE). NOTHING was rolled back automatically." >&2
    echo "!! Investigate first. Before any lesson is published the documented rollback is (run it yourself, only if you decide to roll back):" >&2
    echo "!!   BEGIN; DROP TABLE listening_library_entries; DELETE FROM listening_migrations WHERE version = 14; COMMIT;" >&2
  else
    echo "Nothing was changed by this script (stage: $STAGE). Do not re-run until you know why it stopped." >&2
  fi
}
trap on_exit EXIT

# --- 1. The approved SQL, byte for byte --------------------------------------------------------------------------------------------------------------------
STAGE='sql hash'; [[ -f "$SQL" ]] || stop "missing $SQL"
GOT="$(sha256sum "$SQL" | cut -d' ' -f1)"
[[ "$GOT" == "$APPROVED_SHA256" ]] || stop "docs/postgres-014.sql hash is $GOT, not the approved $APPROVED_SHA256 (wrong branch, or the file changed)"
echo "sql: docs/postgres-014.sql sha256 matches the approved revision"

# --- 2. The verified backup is intact and describes this exact pre-014 state --------------------------------------------------------------------------------
STAGE='backup'
for f in audli-public.dump audli-public.dump.sha256 MANIFEST.txt; do [[ -s "$BACKUP/$f" ]] || stop "backup file missing: $BACKUP/$f"; done
(cd "$BACKUP" && sha256sum -c --quiet audli-public.dump.sha256) || stop 'backup checksum mismatch; the archive changed after it was taken'
grep -qx 'ledger_listening=9,10,11,12  ledger_main=1,2,3,4,5,6,7,8  library_table=absent' "$BACKUP/MANIFEST.txt" || stop 'backup MANIFEST does not describe the expected pre-014 state'
[[ -n "$DISPOSABLE" ]] || grep -q "user=postgres.$PROJECT_REF" "$BACKUP/MANIFEST.txt" || stop 'backup MANIFEST is not from the production project'
echo "backup: $(basename "$BACKUP") checksum OK, manifest = pre-014 state"

# --- 3. Parse the connection WITHOUT echoing it ---------------------------------------------------------------------------------------------------------
STAGE='connection'
eval "$(AUDLI_DISPOSABLE="$DISPOSABLE" AUDLI_PROJECT_REF="$PROJECT_REF" python3 - <<'PY'
import os, shlex, sys
from urllib.parse import urlsplit, unquote
u = urlsplit(os.environ['AUDLI_OWNER_URL'])
def fail(msg):
    print(f'echo {shlex.quote("connection check failed: " + msg)} >&2; exit 1'); sys.exit(0)
ref, disposable = os.environ['AUDLI_PROJECT_REF'], os.environ.get('AUDLI_DISPOSABLE')
if u.scheme not in ('postgres', 'postgresql'): fail('scheme must be postgresql://')
if not (u.hostname and u.username and u.password): fail('URL needs host, user and password')
loopback = u.hostname in ('127.0.0.1', 'localhost', '::1')
port = u.port or 5432
if disposable:
    if not loopback: fail('the disposable-test flag is only honoured for a loopback host')
    ssl = os.environ.get('AUDLI_TEST_SSLMODE') or 'prefer'
else:
    if loopback: fail('loopback host refused outside tests')
    if port == 6543: fail('port 6543 is the TRANSACTION pooler; use the Session Pooler on port 5432')
    if port != 5432: fail(f'port must be 5432, got {port}')
    if not u.hostname.endswith('.pooler.supabase.com'): fail('host is not a Supabase Session Pooler host (*.pooler.supabase.com)')
    if unquote(u.username) != f'postgres.{ref}': fail(f'user must be postgres.{ref} (wrong project or role)')
    ssl = 'require'
db = (u.path or '/postgres').lstrip('/') or 'postgres'
if not disposable and db != 'postgres': fail('database must be postgres')
pairs = {'PGHOST': u.hostname, 'PGPORT': str(port), 'PGUSER': unquote(u.username), 'PGPASSWORD': unquote(u.password), 'PGDATABASE': db, 'PGSSLMODE': ssl, 'PGAPPNAME': 'audli-apply-014'}
for k, v in pairs.items(): print(f'export {k}={shlex.quote(v)}')
print(f'CONN_HOST={shlex.quote(u.hostname)}; CONN_USER={shlex.quote(unquote(u.username))}; CONN_PORT={port}')
PY
)"
unset AUDLI_OWNER_URL
echo "connection: host=$CONN_HOST port=$CONN_PORT user=$CONN_USER (password not shown)"
q() { psql -X -qAt -v ON_ERROR_STOP=1 -c "$1"; }

# --- 4. Preflight on the live database (read-only) -----------------------------------------------------------------------------------------------------
STAGE='preflight'
q "SET default_transaction_read_only = on; SELECT 1" >/dev/null 2>&1 || stop 'cannot connect (check the URL, network and that Docker/VPN is not blocking port 5432)'
PRE="$(q "SELECT concat_ws('|', current_user, (SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='listening_lessons'), pg_is_in_recovery(),
  (current_setting('server_version_num')::int / 10000), current_setting('default_transaction_read_only'), inet_server_port(),
  coalesce((SELECT string_agg(version::text, ',' ORDER BY version) FROM listening_migrations), 'none'),
  coalesce((SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations), 'none'),
  coalesce(to_regclass('public.listening_library_entries')::text, 'absent'),
  (SELECT count(*) FROM listening_lessons), (SELECT count(*) FROM listening_lesson_owners),
  (SELECT count(*) FROM pg_roles WHERE rolname IN ('audli_api_runtime','audli_listening_worker','audli_listening_generator')))")"
IFS='|' read -r P_USER P_OWNER P_REC P_MAJOR P_RO P_PORT P_LED_L P_LED_M P_LIB P_LESSONS P_OWNERS P_ROLES <<<"$PRE"
echo "preflight: role=$P_USER owner=$P_OWNER in_recovery=$P_REC pg=$P_MAJOR read_only=$P_RO port=$P_PORT"
echo "preflight: ledger listening=[$P_LED_L] main=[$P_LED_M] library_table=$P_LIB lessons=$P_LESSONS owners=$P_OWNERS app_roles=$P_ROLES/3"
[[ "$P_USER" == postgres && "$P_OWNER" == postgres ]] || stop "connected role '$P_USER' must be postgres and own the listening tables (owner '$P_OWNER')"
[[ "$P_REC" == f ]] || stop 'server is a standby/recovering; the primary is required'
[[ "$P_RO" == off ]] || stop 'session is read-only'
[[ -n "$DISPOSABLE" ]] || [[ "$P_PORT" == 5432 ]] || stop "server port $P_PORT, expected 5432"
[[ "$P_MAJOR" == 17 ]] || stop "PostgreSQL major $P_MAJOR, expected 17"
[[ "$P_LED_L" == 9,10,11,12 ]] || stop "listening ledger is [$P_LED_L], expected exactly 9,10,11,12 (014 already applied, or another change)"
[[ "$P_LED_M" == 1,2,3,4,5,6,7,8 ]] || stop "main ledger is [$P_LED_M], expected 1..8"
[[ "$P_LIB" == absent ]] || stop 'listening_library_entries already exists'
[[ "$P_ROLES" == 3 ]] || stop 'expected roles audli_api_runtime, audli_listening_worker and audli_listening_generator to exist'

# --- 5. Apply: only the approved file, once, no retry -----------------------------------------------------------------------------------------------------
STAGE='apply'
echo '== applying docs/postgres-014.sql (single transaction) =='
if psql -X -v ON_ERROR_STOP=1 -f "$SQL"; then :; else
  STATE="$(q "SELECT 'ledger=[' || coalesce((SELECT string_agg(version::text, ',' ORDER BY version) FROM listening_migrations),'?') || '] table=' || coalesce(to_regclass('public.listening_library_entries')::text,'absent')" 2>&1 || true)"
  stop "psql reported an error. The SQL is one transaction, so a failure leaves nothing behind. Current state: $STATE. Not retrying."
fi
APPLIED=yes

# --- 6. Verify everything the migration promises ----------------------------------------------------------------------------------------------------------
STAGE='verification'
fail_unless() { [[ "$2" == "$3" ]] || stop "$1: got [$2], expected [$3]"; echo "ok: $1 = $2"; }
fail_unless 'listening ledger' "$(q "SELECT string_agg(version::text, ',' ORDER BY version) FROM listening_migrations")" 9,10,11,12,14
fail_unless 'main ledger unchanged' "$(q "SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations")" 1,2,3,4,5,6,7,8
fail_unless 'row level security' "$(q "SELECT relrowsecurity FROM pg_class WHERE oid='public.listening_library_entries'::regclass")" t
fail_unless 'library rows' "$(q "SELECT count(*) FROM listening_library_entries")" 0
fail_unless 'indexes' "$(q "SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes WHERE schemaname='public' AND tablename='listening_library_entries'")" \
  listening_library_by_curator,listening_library_by_state,listening_library_entries_pkey
fail_unless 'read policy' "$(q "SELECT string_agg(policyname||':'||cmd||':'||roles::text, ',') FROM pg_policies WHERE schemaname='public' AND tablename='listening_library_entries'")" \
  'audli_api_runtime_listening_read:SELECT:{audli_api_runtime}'
# Exact privileges: nobody except the owner may hold anything, apart from audli_api_runtime with SELECT only (PUBLIC would show as grantee 0).
fail_unless 'grants besides the owner' "$(q "SELECT coalesce(string_agg(coalesce(r.rolname,'PUBLIC')||':'||a.privilege_type, ',' ORDER BY r.rolname, a.privilege_type), 'none')
  FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a LEFT JOIN pg_roles r ON r.oid = a.grantee
  WHERE c.oid='public.listening_library_entries'::regclass AND a.grantee <> c.relowner")" audli_api_runtime:SELECT
fail_unless 'api role cannot write' "$(q "SELECT has_table_privilege('audli_api_runtime','listening_library_entries','INSERT') OR has_table_privilege('audli_api_runtime','listening_library_entries','UPDATE') OR has_table_privilege('audli_api_runtime','listening_library_entries','DELETE') OR has_table_privilege('audli_api_runtime','listening_library_entries','TRUNCATE')")" f
fail_unless 'worker/generator have no access' "$(q "SELECT has_table_privilege('audli_listening_worker','listening_library_entries','SELECT,INSERT,UPDATE,DELETE') OR has_table_privilege('audli_listening_generator','listening_library_entries','SELECT,INSERT,UPDATE,DELETE')")" f
fail_unless 'Supabase client roles have no access' "$(q "SELECT coalesce(bool_or(has_table_privilege(rolname,'listening_library_entries','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')), false) FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role')")" f
fail_unless 'lessons unchanged' "$(q "SELECT count(*) FROM listening_lessons")" "$P_LESSONS"
fail_unless 'private-owner rows unchanged' "$(q "SELECT count(*) FROM listening_lesson_owners")" "$P_OWNERS"
STAGE='done'
echo 'RESULT: MIGRATION 014 APPLIED AND VERIFIED (database side). Next: the application checks in docs/LISTENING-LIBRARY-MIGRATION-014.md section 8 step 5. Do not publish yet.'
