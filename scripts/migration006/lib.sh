#!/usr/bin/env bash
# Shared helpers for the migration 006 scripts. Source only. Never enable `set -x` here: credentials live in the environment.
# Categories (each script prints its own banner): READ-ONLY | BACKUP | ISOLATED | PRODUCTION-MUTATING.
set -euo pipefail
set +x

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
MIGRATION_FILE="${AUDLI_MIGRATION_FILE:-$REPO/docs/postgres-006.sql}"
ROLLBACK_FILE="${AUDLI_ROLLBACK_FILE:-$REPO/docs/postgres-006-rollback.sql}"
ROOT="${AUDLI_MIGRATION_DIR:-$HOME/.config/audli/migration006}"
PHRASE_APPLY='APPLY MIGRATION 006 TO PRODUCTION'
PHRASE_ROLLBACK='ROLL BACK MIGRATION 006 ON PRODUCTION'
PROD_VAR=AUDLI_PROD_DATABASE_URL
REHEARSAL_VAR=AUDLI_REHEARSAL_DATABASE_URL
PYTHON_APP="${AUDLI_PYTHON:-$( [ -x "$REPO/.venv/bin/python" ] && echo "$REPO/.venv/bin/python" || command -v python3 )}"

die()    { echo "ERROR: $*" >&2; exit 1; }
info()   { echo "  $*"; }
banner() { echo; echo "=== [$1] $2 ==="; }
need()   { local tool; for tool in "$@"; do command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"; done; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
manifest() { python3 "$HERE/manifest.py" "$@"; }

# Credentials come from the environment, or from a mode-600 env file (default ~/.config/audli/migration006.env) that
# defines AUDLI_PROD_DATABASE_URL and AUDLI_REHEARSAL_DATABASE_URL. Values are never printed.
load_env_file() {
  local file="${ENV_FILE:-$HOME/.config/audli/migration006.env}"
  if [ -f "$file" ]; then
    [ "$(stat -c %a "$file")" = 600 ] || die "$file holds credentials and must have mode 600"
    set +x; . "$file"
  fi
}

# use_target VARIABLE_NAME [DATABASE_OVERRIDE]: export libpq variables; set TARGET_LABEL and TARGET_FP (safe to print).
use_target() {
  local var="$1" env_lines
  env_lines="$(python3 "$HERE/pgenv.py" "$var")" || die "cannot use $var (see message above)"
  eval "$env_lines"
  [ -z "${2:-}" ] || export PGDATABASE="$2"
  TARGET_VAR="$var"
  TARGET_LABEL="$(python3 "$HERE/pgenv.py" "$var" --label)"
  TARGET_FP="$(python3 "$HERE/pgenv.py" "$var" --fingerprint)"
}
redact() { python3 "$HERE/pgenv.py" "$TARGET_VAR" --redact; }

# Read-only session: the server itself refuses writes.
psql_ro() { PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=60000' psql -X -q -At -v ON_ERROR_STOP=1 "$@" 2> >(redact >&2); }
# Read-write session (isolated databases and the production apply/rollback only).
psql_rw() { psql -X -q -v ON_ERROR_STOP=1 "$@" 2> >(redact >&2); }

check_client_versions() {
  local server client
  server="$(psql_ro -c 'show server_version_num')"; server=$(( server / 10000 ))
  client="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
  [ "$client" -ge "$server" ] || die "pg_dump is version $client but the server is $server; install postgresql-client-$server (pg_dump refuses newer servers)"
}

new_run_dir() { local dir="$ROOT/$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$dir"; chmod 700 "$ROOT" "$dir"; echo "$dir"; }
require_run_dir() { [ -d "${RUN_DIR:-}" ] || die "--run-dir is required (the directory printed by 01-preflight.sh)"; }

# The rehearsal server must be loopback (or explicitly allowed), never the production database, never a Supabase host.
require_isolated() {
  local prod_fp rehearsal_fp host
  prod_fp="$(python3 "$HERE/pgenv.py" "$PROD_VAR" --fingerprint)"; rehearsal_fp="$(python3 "$HERE/pgenv.py" "$REHEARSAL_VAR" --fingerprint)"
  host="$(python3 "$HERE/pgenv.py" "$REHEARSAL_VAR" --host)"
  [ "$prod_fp" != "$rehearsal_fp" ] || die "the rehearsal database is the production database; refusing"
  case "$host" in *supabase*) die "the rehearsal host looks like Supabase; refusing";; esac
  case "$host" in localhost|127.0.0.1|::1) ;; *) [ "${ALLOW_REMOTE_REHEARSAL:-0}" = 1 ] || die "the rehearsal host is not loopback; pass --allow-remote-rehearsal only for a disposable server you own";; esac
}

print_lines() { sed 's/^/  /'; }
count_fails() { grep -c '^FAIL|' || true; }

require_flag_value() { [ -n "${2:-}" ] || die "$1 requires a value"; }
