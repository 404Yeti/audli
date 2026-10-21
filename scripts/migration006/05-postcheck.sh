#!/usr/bin/env bash
# [READ-ONLY] Post-migration checks (schema version, entitlement backfill, lesson statuses, constraints, indexes, RLS, trigger,
# counts vs baseline) and the rollback-safety verdict. Works on the rehearsal copy or on production; never writes.
#   05-postcheck.sh --target rehearsal|production --run-dir DIR [--phase migration|observe] [--env-file FILE]
. "$(dirname "$0")/lib.sh"
TARGET=""; PHASE=migration
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --target) require_flag_value "$@"; TARGET="$2"; shift 2;;
  --phase) require_flag_value "$@"; PHASE="$2"; shift 2;;
  *) die "unknown option $1";; esac; done
case "$PHASE" in migration|observe) ;; *) die "--phase must be migration or observe";; esac
need psql python3; require_run_dir; load_env_file
case "$TARGET" in
  production) use_target "$PROD_VAR";;
  rehearsal) [ -f "$RUN_DIR/restore.json" ] || die "no restore.json in $RUN_DIR: run 03-restore-verify.sh first"
             use_target "$REHEARSAL_VAR" "$(manifest get "$RUN_DIR/restore.json" rehearsal_database)";;
  *) die "--target must be rehearsal or production";; esac
banner READ-ONLY "post-migration checks on $TARGET (phase: $PHASE; no writes are possible)"
info "target: $TARGET_LABEL"
if [ "$(psql_ro -c "SELECT to_regclass('public.lesson_entitlements') IS NOT NULL")" != t ]; then
  echo "FAIL|ledger_table_present|migration 006 has not been applied here" | print_lines; exit 1
fi
OUT="$(psql_ro -v phase="$PHASE" -f "$HERE/sql/postcheck.sql")"
echo "$OUT" | print_lines
FAILS="$(echo "$OUT" | count_fails)"
if [ "$PHASE" = migration ]; then
  psql_ro -f "$HERE/sql/counts.sql" > "$RUN_DIR/after-counts-$TARGET.txt"
  if [ "$TARGET" = rehearsal ]; then
    # The restored copy is static: every count, lesson status and the lesson-row checksum must equal the dump's own snapshot.
    # (the migration ledger itself legitimately gains version 6, so it is excluded from the comparison)
    DIFF="$(diff <(grep -vE '^(schema_versions\||rows:schema_migrations\|)' "$RUN_DIR/backup-counts.txt") <(grep -vE '^(schema_versions\||rows:schema_migrations\|)' "$RUN_DIR/after-counts-$TARGET.txt") || true)"
    if [ -z "$DIFF" ]; then echo "  PASS|historical_data_unchanged|every table count, lesson status and the lesson-row checksum equal the dump snapshot"
    else echo "  FAIL|historical_data_unchanged|differs from the dump snapshot:"; echo "$DIFF" | print_lines; FAILS=$((FAILS+1)); fi
  else
    # Production is live: compare with the counts read immediately before the apply and flag only LOST rows (traffic only adds rows).
    [ -f "$RUN_DIR/pre-apply-counts.txt" ] || die "no pre-apply-counts.txt (06-apply-production.sh writes it just before applying)"
    LOST="$(python3 - "$RUN_DIR/pre-apply-counts.txt" "$RUN_DIR/after-counts-$TARGET.txt" <<'PY'
import sys
read = lambda p: {k: v for k, v in (l.rstrip('\n').split('|', 1) for l in open(p) if '|' in l) if k.startswith('rows:')}
before, after = read(sys.argv[1]), read(sys.argv[2])
for name, count in before.items():
    if int(after.get(name, -1)) < int(count):
        print(f'{name}: {count} before, {after.get(name, "missing")} after')
PY
)"
    if [ -z "$LOST" ]; then echo "  PASS|no_rows_lost|no table has fewer rows than immediately before the apply (live traffic may add rows)"
    else echo "  FAIL|no_rows_lost|rows disappeared:"; echo "$LOST" | print_lines; FAILS=$((FAILS+1)); fi
  fi
fi
echo
echo "--- rollback safety (is docs/postgres-006-rollback.sql still safe?) ---"
SAFETY="$(psql_ro -f "$HERE/sql/rollback_safety.sql")"
echo "$SAFETY" | print_lines
VERDICT="$(echo "$SAFETY" | grep -E '^(SAFE|UNSAFE)\|' | cut -d'|' -f1)"
manifest write "$RUN_DIR/postcheck-$TARGET.json" result="$([ "$FAILS" = 0 ] && echo PASS || echo FAIL)" rollback_sql_safe="$VERDICT" phase="$PHASE"
echo
if [ "$FAILS" = 0 ]; then echo "POSTCHECK PASS on $TARGET. SQL rollback safe: $VERDICT."
else
  echo "POSTCHECK FAIL on $TARGET: $FAILS check(s). SQL rollback safe: $VERDICT. NOTHING WAS CHANGED BY THIS SCRIPT." >&2
  echo "Decide with the owner: if SAFE, 07-rollback-production.sh is available (explicit approval required); if UNSAFE, use AUDLI_LESSON_LIMIT_MODE=off." >&2
  exit 1
fi
