#!/usr/bin/env bash
# [READ-ONLY] Database half of the observe-mode monitoring (the log half is in docs/AUD-47-OBSERVE-RUNBOOK.md). Prints one line per
# metric and exits 1 on any ALERT. The server refuses writes in this session.
#   08-observe-report.sh --since 2026-10-22T02:00:00Z [--mode observe|enforce] [--target production|rehearsal] [--run-dir DIR] [--env-file FILE]
. "$(dirname "$0")/lib.sh"
SINCE=""; TARGET=production; MODE=observe
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --since) require_flag_value "$@"; SINCE="$2"; shift 2;;
  --mode) require_flag_value "$@"; MODE="$2"; shift 2;;
  --target) require_flag_value "$@"; TARGET="$2"; shift 2;;
  *) die "unknown option $1";; esac; done
[ -n "$SINCE" ] || die "--since is required (the UTC time observe was activated)"
case "$MODE" in observe) ENFORCE=0;; enforce) ENFORCE=1;; *) die "--mode must be observe or enforce";; esac
need psql python3; load_env_file
case "$TARGET" in
  production) use_target "$PROD_VAR";;
  rehearsal) require_run_dir; use_target "$REHEARSAL_VAR" "$(manifest get "$RUN_DIR/restore.json" rehearsal_database)";;
  *) die "--target must be production or rehearsal";; esac
banner READ-ONLY "$MODE-mode database report on $TARGET since $SINCE (no writes are possible)"
info "target: $TARGET_LABEL   now: $(psql_ro -c 'select now()')"
OUT="$(psql_ro -F'|' -v since="$SINCE" -v enforce="$ENFORCE" -f "$HERE/sql/observe_report.sql")"
echo "$OUT" | sed 's/^/  /'
ALERTS="$(echo "$OUT" | grep -c '^ALERT|' || true)"
if [ "$ALERTS" = 0 ]; then echo "OBSERVE REPORT OK (database side)"; else echo "OBSERVE REPORT: $ALERTS ALERT line(s); see the stop conditions in docs/AUD-47-OBSERVE-RUNBOOK.md" >&2; exit 1; fi
