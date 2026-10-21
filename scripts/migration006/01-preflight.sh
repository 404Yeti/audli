#!/usr/bin/env bash
# [READ-ONLY] Preflight for migration 006 against PRODUCTION. Opens a read-only session (the server rejects writes),
# checks the schema is in the expected pre-006 shape, records the baseline counts, and fingerprints the migration file.
#   01-preflight.sh [--env-file FILE] [--run-dir DIR]
. "$(dirname "$0")/lib.sh"
RUN_DIR=""
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  *) die "unknown option $1";; esac; done
banner READ-ONLY "preflight (no writes are possible in this session)"
need psql pg_dump python3 sha256sum
load_env_file; use_target "$PROD_VAR"
info "target: $TARGET_LABEL  fingerprint: $TARGET_FP"
[ -n "$RUN_DIR" ] || RUN_DIR="$(new_run_dir)"; mkdir -p "$RUN_DIR"; chmod 700 "$RUN_DIR"
[ -f "$MIGRATION_FILE" ] || die "migration file not found: $MIGRATION_FILE"
SHA="$(sha256_of "$MIGRATION_FILE")"
info "migration file: $MIGRATION_FILE"; info "sha256: $SHA ($(wc -l < "$MIGRATION_FILE") lines)"
check_client_versions
OUT="$(psql_ro -f "$HERE/sql/preflight.sql")"
echo "$OUT" | print_lines
psql_ro -f "$HERE/sql/counts.sql" > "$RUN_DIR/baseline-counts.txt"; chmod 600 "$RUN_DIR/baseline-counts.txt"
FAILS="$(echo "$OUT" | count_fails)"
RESULT=PASS; [ "$FAILS" = 0 ] || RESULT=FAIL
manifest write "$RUN_DIR/preflight.json" result="$RESULT" source_fingerprint="$TARGET_FP" migration_sha256="$SHA" \
  server_version="$(echo "$OUT" | sed -n 's/^INFO|server_version|//p')" learners_with_lessons="$(echo "$OUT" | sed -n 's/^INFO|learners_with_lessons|//p')"
echo
info "run directory: $RUN_DIR"
if [ "$RESULT" = PASS ]; then echo "PREFLIGHT PASS (production was only read)"; else echo "PREFLIGHT FAIL: $FAILS check(s) failed; do not continue" >&2; exit 1; fi
