#!/usr/bin/env bash
# [PRODUCTION-MUTATING] Applies docs/postgres-006-rollback.sql to PRODUCTION. MANUAL USE ONLY: no other script calls this.
# Runs only with the exact phrase, the rollback file hash, the production fingerprint AND a live SAFE verdict from
# sql/rollback_safety.sql (no 'expired' lessons, no voided allowances, no grace slots or first-speech failures: i.e. enforce mode never wrote).
# If the verdict is UNSAFE the rollback is AUDLI_LESSON_LIMIT_MODE=off, not this file.
#   07-rollback-production.sh --run-dir DIR --expected-sha256 ROLLBACK_FILE_SHA --target-fingerprint FP --confirm "PHRASE" [--dry-run]
. "$(dirname "$0")/lib.sh"
EXPECTED=""; WANT_FP=""; CONFIRM=""; DRY=0
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --expected-sha256) require_flag_value "$@"; EXPECTED="$2"; shift 2;;
  --target-fingerprint) require_flag_value "$@"; WANT_FP="$2"; shift 2;;
  --confirm) require_flag_value "$@"; CONFIRM="$2"; shift 2;;
  --dry-run) DRY=1; shift;;
  *) die "unknown option $1";; esac; done
banner PRODUCTION-MUTATING "roll back migration 006 on PRODUCTION$([ "$DRY" = 1 ] && echo ' (DRY RUN: nothing will be written)')"
need psql python3 sha256sum; require_run_dir; load_env_file; use_target "$PROD_VAR"
info "target: $TARGET_LABEL  fingerprint: $TARGET_FP"
[ "$WANT_FP" = "$TARGET_FP" ] || die "--target-fingerprint does not match this database ($TARGET_FP)"
ACTUAL="$(sha256_of "$ROLLBACK_FILE")"; info "rollback file sha256: $ACTUAL"
[ -n "$EXPECTED" ] && [ "$EXPECTED" = "$ACTUAL" ] || die "rollback file hash mismatch: expected '${EXPECTED:-<none>}', file is $ACTUAL"
[ "$DRY" = 1 ] || [ "$CONFIRM" = "$PHRASE_ROLLBACK" ] || die "refusing: pass --confirm \"$PHRASE_ROLLBACK\" to roll back production"
[ "$(psql_ro -c "SELECT to_regclass('public.lesson_entitlements') IS NOT NULL")" = t ] || die "migration 006 is not applied here; nothing to roll back"
SAFETY="$(psql_ro -f "$HERE/sql/rollback_safety.sql")"; echo "$SAFETY" | print_lines
echo "$SAFETY" | grep -q '^SAFE|' || die "the SQL rollback is NOT safe (see UNSAFE line); use AUDLI_LESSON_LIMIT_MODE=off instead. Production is unchanged."
if [ "$DRY" = 1 ]; then echo; echo "DRY RUN COMPLETE: the rollback would be applied. Nothing was written."; exit 0; fi
info "APPLYING the rollback (single transaction; lock_timeout 5s, statement_timeout 30s)"
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s' psql_rw -f "$ROLLBACK_FILE" >/dev/null || die "the rollback failed; it is one transaction, so production is unchanged"
manifest write "$RUN_DIR/rollback.json" result=PASS source_fingerprint="$TARGET_FP" rollback_sha256="$ACTUAL"
psql_ro -f "$HERE/sql/preflight.sql" | print_lines
echo; echo "ROLLED BACK. Verify the lines above are all PASS; the application keeps working (mode off never used the ledger)."
