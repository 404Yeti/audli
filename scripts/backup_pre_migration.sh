#!/usr/bin/env bash
# Pre-migration logical backup of the production `public` schema (schema + data + grants/ACLs + migration ledgers), read-only.
#
#   export AUDLI_OWNER_URL='postgresql://postgres.<ref>:<password>@<pooler-host>:5432/postgres'   # type it yourself; never paste it into chat, a commit or a log
#   scripts/backup_pre_migration.sh [OUTPUT_DIR]          # default: ~/audli-backups (outside the repo)
#
# Safety: reads only (pg_dump + SELECTs). The URL is parsed into libpq environment variables inside this process; it is never printed, never put on a command
# line and `set -x` is refused. Port 6543 (transaction pooler) is refused. The output holds learner data: directory mode 700, files 600, keep it private,
# delete it after the verification window.
set -euo pipefail
case "$-" in *x*) echo 'refusing to run with xtrace enabled (it would print the credentials)' >&2; exit 2;; esac
umask 077
EXPECTED_REF="${AUDLI_EXPECTED_PROJECT_REF:-fopyknjekizcgrazisnq}"
: "${AUDLI_OWNER_URL:?set AUDLI_OWNER_URL to the owner (postgres) Session Pooler URL, port 5432}"

# 1. Parse and sanity-check the connection WITHOUT echoing it.
eval "$(python3 - <<'PY'
import os, shlex, sys
from urllib.parse import urlsplit, unquote
u = urlsplit(os.environ['AUDLI_OWNER_URL'])
def fail(msg):
    print(f'echo {shlex.quote("connection check failed: " + msg)} >&2; exit 2'); sys.exit(0)
if u.scheme not in ('postgres', 'postgresql'): fail('scheme must be postgresql://')
if not (u.hostname and u.username and u.password): fail('URL needs host, user and password')
port = u.port or 5432
if port == 6543: fail('port 6543 is the TRANSACTION pooler; use the Session Pooler (port 5432) or the direct host')
if port != 5432: fail(f'unexpected port {port}; expected 5432')
db = (u.path or '/postgres').lstrip('/') or 'postgres'
loopback = u.hostname in ('127.0.0.1', 'localhost', '::1')
ssl = os.environ.get('AUDLI_TEST_SSLMODE') if loopback and os.environ.get('AUDLI_TEST_SSLMODE') else 'require'   # only a loopback test server may relax TLS
pairs = {'PGHOST': u.hostname, 'PGPORT': str(port), 'PGUSER': unquote(u.username), 'PGPASSWORD': unquote(u.password), 'PGDATABASE': db, 'PGSSLMODE': ssl, 'PGAPPNAME': 'audli-pre014-backup'}
for k, v in pairs.items(): print(f'export {k}={shlex.quote(v)}')
kind = 'session-pooler' if 'pooler.supabase.com' in u.hostname else ('direct' if u.hostname.startswith('db.') else 'other')
print(f'CONN_KIND={kind}; CONN_HOST={shlex.quote(u.hostname)}; CONN_USER={shlex.quote(unquote(u.username))}; CONN_PORT={port}')
PY
)"
unset AUDLI_OWNER_URL
echo "connection: kind=$CONN_KIND host=$CONN_HOST port=$CONN_PORT user=$CONN_USER (password not shown)"
case "$CONN_USER" in postgres."$EXPECTED_REF"|postgres) ;; *) echo "user is not postgres[.${EXPECTED_REF}]: wrong project or role; stopping" >&2; exit 2;; esac
[[ "$CONN_KIND" == other ]] && echo 'warning: host is neither the Supabase pooler nor db.<ref> direct host; confirm it is right'

q() { psql -X -qAt -v ON_ERROR_STOP=1 -c "$1"; }

# 2. Identity, version compatibility and state (all read-only).
IDENT="$(q "SELECT current_user || '|' || current_database() || '|' || (SELECT tableowner FROM pg_tables WHERE tablename='listening_lessons') || '|' || pg_is_in_recovery() || '|' || current_setting('server_version_num') || '|' || current_setting('transaction_isolation')")"
IFS='|' read -r CUR_USER CUR_DB OWNER IN_REC SRV_NUM ISO <<<"$IDENT"
echo "identity: current_user=$CUR_USER db=$CUR_DB listening_owner=$OWNER in_recovery=$IN_REC server_version_num=$SRV_NUM"
[[ "$CUR_USER" == "$OWNER" ]] || { echo 'connected role is not the table owner; the owner connection is required' >&2; exit 2; }
[[ "$IN_REC" == false ]] || { echo 'server is in recovery (replica); stopping' >&2; exit 2; }
SRV_MAJOR=$((SRV_NUM / 10000)); CLI_MAJOR="$(pg_dump --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')"
echo "pg_dump major=$CLI_MAJOR server major=$SRV_MAJOR"
(( CLI_MAJOR >= SRV_MAJOR )) || { echo 'pg_dump is older than the server; install a matching postgresql-client' >&2; exit 2; }
LEDGER_L="$(q "SELECT coalesce(string_agg(version::text, ',' ORDER BY version), '') FROM listening_migrations")"
LEDGER_M="$(q "SELECT coalesce(string_agg(version::text, ',' ORDER BY version), '') FROM schema_migrations")"
LIB="$(q "SELECT coalesce(to_regclass('public.listening_library_entries')::text, 'absent')")"
echo "ledger: listening=[$LEDGER_L] main=[$LEDGER_M] library_table=$LIB"
[[ "$LEDGER_L" == 9,10,11,12 && "$LIB" == absent ]] || echo 'note: the ledger is not the expected pre-014 state (9,10,11,12 and table absent); a backup is still taken'

# 3. Dump, in ONE repeatable-read snapshot: the fingerprint (object counts + a content hash per table) is computed inside the same snapshot that pg_dump
#    reads (`pg_dump --snapshot`), so live writes during the backup cannot make the restore comparison fail falsely.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"; OUT="${1:-$HOME/audli-backups}/pre014-$STAMP"
mkdir -p -m 700 "$OUT"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AUDLI_OUT="$OUT"
{
  echo 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;'
  echo 'SELECT pg_export_snapshot() AS snap \gset'
  echo '\setenv AUDLI_SNAPSHOT :snap'
  echo '\o | cat > "$AUDLI_OUT/fingerprint.txt"'
  cat "$HERE/pre_migration_fingerprint.sql"
  echo '\o'
  echo '\! pg_dump --format=custom --compress=6 --schema=public --no-password --snapshot="$AUDLI_SNAPSHOT" --file="$AUDLI_OUT/audli-public.dump" || touch "$AUDLI_OUT/DUMP-FAILED"'
  echo 'COMMIT;'
} | psql -X -qAt -v ON_ERROR_STOP=1
[[ ! -e "$OUT/DUMP-FAILED" && -s "$OUT/audli-public.dump" && -s "$OUT/fingerprint.txt" ]] || { echo 'pg_dump or the fingerprint failed; nothing usable was written' >&2; exit 1; }

# 4. Roles, memberships, default ACLs and table grants as data (roles and passwords cannot be dumped on Supabase; this records what to recreate).
{
  echo '# roles (no passwords)'; q "SELECT rolname, rolcanlogin, rolinherit, rolcreaterole, rolcreatedb, rolbypassrls, rolsuper, rolconnlimit FROM pg_roles WHERE rolname !~ '^(pg_|supabase_|pgbouncer|cli_login|dashboard_user)' ORDER BY 1"
  echo '# memberships'; q "SELECT r.rolname || ' -> ' || m.rolname FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.member JOIN pg_roles m ON m.oid=a.roleid WHERE r.rolname !~ '^(pg_|supabase_|pgbouncer|cli_login|dashboard_user)' ORDER BY 1"
  echo '# default ACLs (public)'; q "SELECT pg_get_userbyid(defaclrole) || ' ' || defaclobjtype::text || ' ' || defaclacl::text FROM pg_default_acl d JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE n.nspname='public' ORDER BY 1"
  echo '# table grants on listening_* (grantee, table, privilege)'; q "SELECT grantee || ' ' || table_name || ' ' || string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants WHERE table_schema='public' AND table_name LIKE 'listening\\_%' GROUP BY grantee, table_name ORDER BY 1"
} > "$OUT/roles-and-grants.txt"

# 5. Integrity: the archive must parse end to end, and the manifest records its hash.
pg_restore --list "$OUT/audli-public.dump" > "$OUT/toc.txt"
pg_restore --file=/dev/null "$OUT/audli-public.dump"
ENTRIES="$(grep -vc '^;' "$OUT/toc.txt" || true)"; BYTES="$(stat -c %s "$OUT/audli-public.dump")"
(cd "$OUT" && sha256sum audli-public.dump > audli-public.dump.sha256 && sha256sum -c --quiet audli-public.dump.sha256)
cat > "$OUT/MANIFEST.txt" <<MAN
taken_utc=$STAMP
connection_kind=$CONN_KIND  host=$CONN_HOST  user=$CONN_USER
server_version_num=$SRV_NUM  pg_dump_major=$CLI_MAJOR
ledger_listening=$LEDGER_L  ledger_main=$LEDGER_M  library_table=$LIB
dump_bytes=$BYTES  toc_entries=$ENTRIES  fingerprint_sha256=$(sha256sum "$OUT/fingerprint.txt" | cut -d' ' -f1)
sha256=$(cut -d' ' -f1 "$OUT/audli-public.dump.sha256")
MAN
chmod 600 "$OUT"/*
echo "backup OK: $OUT  (${BYTES} bytes, ${ENTRIES} TOC entries, archive parsed end to end, sha256 recorded)"
echo 'next: scripts/verify_restore_pre_migration.sh "'"$OUT"'"   (needs Docker running)'
