#!/usr/bin/env bash
# [PRODUCTION-MUTATING] Applies docs/postgres-006.sql to PRODUCTION. Runs only when ALL of these hold:
#   * --confirm equals the exact phrase  APPLY MIGRATION 006 TO PRODUCTION
#   * --expected-sha256 equals the sha256 of the migration file on disk (the value reviewed and recorded by 01-preflight.sh)
#   * --target-fingerprint equals the fingerprint of the production URL (printed by 01-preflight.sh; stops wrong-database mistakes)
#   * manifests for preflight, backup, restore and rehearsal exist, PASSED, belong to this file and this database, and are < N hours old
#   * a fresh read-only preflight against production still passes
# The file is one transaction: any failure leaves production unchanged. This script NEVER restores or rolls back by itself.
#   06-apply-production.sh --run-dir DIR --expected-sha256 H --target-fingerprint FP --confirm "PHRASE" [--max-age-hours 24] [--dry-run]
. "$(dirname "$0")/lib.sh"
EXPECTED=""; WANT_FP=""; CONFIRM=""; MAX_AGE=24; DRY=0
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --expected-sha256) require_flag_value "$@"; EXPECTED="$2"; shift 2;;
  --target-fingerprint) require_flag_value "$@"; WANT_FP="$2"; shift 2;;
  --confirm) require_flag_value "$@"; CONFIRM="$2"; shift 2;;
  --max-age-hours) require_flag_value "$@"; MAX_AGE="$2"; shift 2;;
  --dry-run) DRY=1; shift;;
  *) die "unknown option $1";; esac; done
banner PRODUCTION-MUTATING "apply migration 006 to PRODUCTION$([ "$DRY" = 1 ] && echo ' (DRY RUN: nothing will be written)')"
need psql python3 sha256sum; require_run_dir; load_env_file; use_target "$PROD_VAR"
info "target: $TARGET_LABEL  fingerprint: $TARGET_FP"
[ "$WANT_FP" = "$TARGET_FP" ] || die "--target-fingerprint does not match this database ($TARGET_FP); you may be pointing at the wrong database"
ACTUAL="$(sha256_of "$MIGRATION_FILE")"
info "migration file sha256: $ACTUAL"
[ -n "$EXPECTED" ] && [ "$EXPECTED" = "$ACTUAL" ] || die "migration file hash mismatch: expected '${EXPECTED:-<none>}', file is $ACTUAL"
[ "$DRY" = 1 ] || [ "$CONFIRM" = "$PHRASE_APPLY" ] || die "refusing: pass --confirm \"$PHRASE_APPLY\" to apply to production"
manifest require "$RUN_DIR" --migration-sha "$ACTUAL" --fingerprint "$TARGET_FP" --max-age-hours "$MAX_AGE" preflight backup restore rehearsal \
  || die "the rehearsal evidence is missing, failed, stale, or for another file/database (details above)"
[ "$(manifest get "$RUN_DIR/restore.json" dump_sha256)" = "$(manifest get "$RUN_DIR/backup.json" dump_sha256)" ] \
  && [ "$(manifest get "$RUN_DIR/rehearsal.json" rehearsal_database)" = "$(manifest get "$RUN_DIR/restore.json" rehearsal_database)" ] \
  || die "the restore and rehearsal evidence do not describe the same backup; repeat 02, 03 and 04 in one run directory"
info "evidence ok: preflight, backup, restore verification and rehearsal all PASSED for this file and this database"
info "backup: $(manifest get "$RUN_DIR/backup.json" bytes) bytes, sha256 $(manifest get "$RUN_DIR/backup.json" dump_sha256)"
info "rehearsal apply time: $(manifest get "$RUN_DIR/rehearsal.json" apply_seconds)s"
LIVE="$(psql_ro -f "$HERE/sql/preflight.sql")"; echo "$LIVE" | print_lines
[ "$(echo "$LIVE" | count_fails)" = 0 ] || die "the live preflight failed just now; production is unchanged"
if [ "$DRY" = 1 ]; then echo; echo "DRY RUN COMPLETE: every gate passed; with --confirm the file above would be applied. Nothing was written."; exit 0; fi
psql_ro -f "$HERE/sql/counts.sql" > "$RUN_DIR/pre-apply-counts.txt"; chmod 600 "$RUN_DIR/pre-apply-counts.txt"
echo; info "APPLYING (single transaction; lock_timeout 5s, statement_timeout 30s)"
T0="$(date +%s.%N)"
if PROD_WRITE_OK=1 psql_apply -f "$MIGRATION_FILE" >/dev/null; then
  TOOK="$(python3 -c "print(round($(date +%s.%N) - $T0, 3))")"
  manifest write "$RUN_DIR/apply.json" result=PASS source_fingerprint="$TARGET_FP" migration_sha256="$ACTUAL" seconds="$TOOK"
  info "applied in ${TOOK}s; running the read-only post-checks"
  if "$HERE/05-postcheck.sh" --target production --run-dir "$RUN_DIR" --phase migration; then
    echo; echo "MIGRATION 006 APPLIED AND VERIFIED. The lesson limit is still OFF; nothing else changed."
  else
    echo; echo "MIGRATION 006 WAS APPLIED BUT A POST-CHECK FAILED. No rollback or restore was attempted." >&2
    echo "Read the rollback-safety line above, then ask the owner. If SAFE the rollback is 07-rollback-production.sh (explicit approval)." >&2
    exit 1
  fi
else
  manifest write "$RUN_DIR/apply.json" result=FAIL source_fingerprint="$TARGET_FP" migration_sha256="$ACTUAL"
  echo "The migration FAILED. It is one transaction, so production is unchanged (verify with 01-preflight.sh). No rollback or restore was attempted." >&2
  exit 1
fi
