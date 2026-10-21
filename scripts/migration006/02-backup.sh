#!/usr/bin/env bash
# [BACKUP] Reads PRODUCTION (read-only session, one consistent snapshot) and writes a local custom-format dump. Nothing in
# production is modified. The dump and the baseline counts come from the SAME snapshot, so the restore check can be exact.
#   02-backup.sh --run-dir DIR [--env-file FILE]
. "$(dirname "$0")/lib.sh"
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  *) die "unknown option $1";; esac; done
banner BACKUP "pg_dump of PRODUCTION (read-only snapshot) into a local file"
need psql pg_dump pg_restore python3 sha256sum; require_run_dir
load_env_file; use_target "$PROD_VAR"
info "target: $TARGET_LABEL  fingerprint: $TARGET_FP"
SHA="$(sha256_of "$MIGRATION_FILE")"
manifest require "$RUN_DIR" --migration-sha "$SHA" --fingerprint "$TARGET_FP" --max-age-hours 6 preflight \
  || die "run 01-preflight.sh first (it must have PASSED against this database within 6 hours)"
check_client_versions
DUMP="$RUN_DIR/backup.dump"; [ ! -e "$DUMP" ] || die "$DUMP already exists; use a new run directory"
# One read-only REPEATABLE READ session exports a snapshot; pg_dump and the counts both read it.
coproc SNAP { PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=0' psql -X -q -At -v ON_ERROR_STOP=1 2> >(redact >&2); }
trap 'kill "$SNAP_PID" 2>/dev/null || true' EXIT
echo 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;' >&"${SNAP[1]}"
echo 'SELECT pg_export_snapshot();' >&"${SNAP[1]}"
read -r SNAPSHOT <&"${SNAP[0]}" || die "could not export a snapshot"
info "snapshot exported; dumping schema public (no owners/privileges, for portable restore)"
pg_dump --format=custom --schema=public --no-owner --no-privileges --snapshot="$SNAPSHOT" --file="$DUMP" 2> >(redact >&2)
chmod 600 "$DUMP"
python3 - "$HERE/sql/counts.sql" >&"${SNAP[1]}" <<'PY'
import sys
print(open(sys.argv[1]).read())
print("SELECT 'END_OF_COUNTS';")
PY
: > "$RUN_DIR/backup-counts.txt"
while read -r LINE <&"${SNAP[0]}"; do [ "$LINE" = END_OF_COUNTS ] && break; echo "$LINE" >> "$RUN_DIR/backup-counts.txt"; done
echo 'ROLLBACK;' >&"${SNAP[1]}"; echo '\q' >&"${SNAP[1]}"; wait "$SNAP_PID" 2>/dev/null || true; trap - EXIT
chmod 600 "$RUN_DIR/backup-counts.txt"
BYTES="$(stat -c %s "$DUMP")"; DUMP_SHA="$(sha256_of "$DUMP")"
TOC="$(pg_restore --list "$DUMP" | grep -vc '^;' || true)"
[ "$BYTES" -gt 0 ] && [ "$TOC" -gt 0 ] || die "the dump is empty or unreadable"
info "dump: $DUMP"; info "bytes: $BYTES  sha256: $DUMP_SHA  table-of-contents entries: $TOC"
info "counts at snapshot: $(grep -c '^rows:' "$RUN_DIR/backup-counts.txt") tables, $(sed -n 's/^learners_with_lessons|//p' "$RUN_DIR/backup-counts.txt") learners with lessons"
manifest write "$RUN_DIR/backup.json" result=PASS source_fingerprint="$TARGET_FP" migration_sha256="$SHA" bytes="$BYTES" dump_sha256="$DUMP_SHA" toc_entries="$TOC"
echo "BACKUP WRITTEN. It is NOT proven restorable until 03-restore-verify.sh passes."
