#!/usr/bin/env bash
# [ISOLATED] Proves the backup is restorable: creates a NEW database on the rehearsal server, restores the dump into it and
# compares every table count, the lesson status distribution, the lesson-row checksum and the schema versions with the
# counts taken from the same snapshot as the dump. Production is never contacted.
#   03-restore-verify.sh --run-dir DIR [--env-file FILE] [--allow-remote-rehearsal]
. "$(dirname "$0")/lib.sh"   # migration 007 helpers (which source the reviewed 006 ones)
ALLOW_REMOTE_REHEARSAL=0
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --allow-remote-rehearsal) ALLOW_REMOTE_REHEARSAL=1; shift;;
  *) die "unknown option $1";; esac; done
banner ISOLATED "restore the backup into a brand-new database on the rehearsal server"
need psql pg_restore python3 sha256sum; require_run_dir
load_env_file
FP="$(python3 "$HERE/pgenv.py" "$PROD_VAR" --fingerprint)"   # fingerprint only; production is not contacted
require_isolated
SHA="$(sha256_of "$MIGRATION_FILE")"
manifest require "$RUN_DIR" --migration-sha "$SHA" --fingerprint "$FP" --max-age-hours 24 backup || die "no valid backup manifest in $RUN_DIR"
DUMP="$RUN_DIR/backup.dump"
[ "$(sha256_of "$DUMP")" = "$(manifest get "$RUN_DIR/backup.json" dump_sha256)" ] || die "the dump file changed since it was written"
use_target "$REHEARSAL_VAR" postgres
info "rehearsal server: $TARGET_LABEL"
require_rehearsal_not_older
client="$(pg_restore --version | grep -oE '[0-9]+' | head -1)"; [ "$client" -ge "$(manifest get "$RUN_DIR/preflight.json" server_version | grep -oE '^[0-9]+')" ] || die "pg_restore is version $client, older than the production server; install a newer postgresql-client"
DB="audli_rehearsal_$(date -u +%Y%m%dT%H%M%S)_$$"
psql_rw -c "CREATE DATABASE \"$DB\"" >/dev/null
info "created database $DB; restoring (exit on first error)"
use_target "$REHEARSAL_VAR" "$DB"
psql_rw -c 'DROP SCHEMA public CASCADE' >/dev/null   # the dump recreates schema public itself (new, empty, isolated database only)
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$DB" "$DUMP" 2> >(redact >&2)
psql_ro -f "$COUNTS" > "$RUN_DIR/restored-counts.txt"
if diff -u "$RUN_DIR/backup-counts.txt" "$RUN_DIR/restored-counts.txt" > "$RUN_DIR/restore-diff.txt"; then
  info "all $(grep -c '^rows:' "$RUN_DIR/restored-counts.txt") table counts, lesson statuses, lesson-row checksum and schema versions match the backup snapshot"
  manifest write "$RUN_DIR/restore.json" result=PASS source_fingerprint="$FP" migration_sha256="$SHA" rehearsal_database="$DB" dump_sha256="$(sha256_of "$DUMP")"
  echo "RESTORE VERIFIED into $DB. The backup is restorable."
else
  cat "$RUN_DIR/restore-diff.txt" | head -40 >&2
  manifest write "$RUN_DIR/restore.json" result=FAIL source_fingerprint="$FP" rehearsal_database="$DB"
  die "restored data differs from the backup snapshot (see $RUN_DIR/restore-diff.txt); do not continue"
fi
