#!/usr/bin/env bash
# [ISOLATED] Removes the temporary copy of production data made for the migration 007 rehearsal: the dump and counts in the run directory
# and the restored database(s) on the isolated rehearsal server. It NEVER contacts production (it only reads the production fingerprint to
# prove the rehearsal server is a different database) and it can only delete:
#   * one run directory that is a direct child of the migration 007 root (~/.config/audli/migration007/<UTC timestamp>), and
#   * the databases named in that run's restore manifest, on the rehearsal server (loopback, audli_rehearsal_* names only).
# It refuses anything else, including the production database and the existing backups (~/.config/audli/backups, migration006 runs).
#   04-cleanup.sh --run-dir DIR --reported --confirm "DELETE RESTORED PRODUCTION COPY" [--dry-run] [--env-file FILE]
. "$(dirname "$0")/lib.sh"
PHRASE_CLEANUP='DELETE RESTORED PRODUCTION COPY'
CONFIRM=""; REPORTED=0; DRY=0
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --confirm) require_flag_value "$@"; CONFIRM="$2"; shift 2;;
  --reported) REPORTED=1; shift;;
  --dry-run) DRY=1; shift;;
  *) die "unknown option $1";; esac; done
banner ISOLATED "delete the restored production copy and the temporary backup (production is not contacted)"
need psql python3 realpath; require_run_dir
REAL_ROOT="$(realpath -m "$ROOT")"; REAL_RUN="$(realpath -m "$RUN_DIR")"
[ ! -L "$RUN_DIR" ] || die "the run directory is a symbolic link; refusing"
[ "$(dirname "$REAL_RUN")" = "$REAL_ROOT" ] || die "the run directory must be a direct child of $REAL_ROOT; refusing to delete anything else"
case "$(basename "$REAL_RUN")" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;; *) die "the run directory name is not a UTC timestamp; refusing";; esac
case "$REAL_RUN" in *backups*|*migration006*) die "that path belongs to the existing backups or the migration 006 runs; refusing";; esac
[ "$(manifest get "$RUN_DIR/restore.json" result 2>/dev/null)" = PASS ] || die "no PASSED restore manifest: the backup was not verified, so nothing is deleted"
python3 - "$RUN_DIR/rehearsal-report.json" <<'PY' || die "no PASSED rehearsal report in the run directory (rehearse.py --out \$RUN/rehearsal-report.json); nothing is deleted before the rehearsal has passed"
import json, sys
report = json.load(open(sys.argv[1]))
sys.exit(0 if report.get('completed') is True and report.get('failed') == 0 and report.get('passed', 0) > 0 else 1)
PY
[ "$REPORTED" = 1 ] || die "pass --reported only after the results have been reported; the copy is the evidence until then"
[ "$CONFIRM" = "$PHRASE_CLEANUP" ] || die "the confirmation phrase is missing or wrong"
DB="$(manifest get "$RUN_DIR/restore.json" rehearsal_database)"
case "$DB" in audli_rehearsal_[0-9]*) ;; *) die "the restored database name in the manifest is not an audli_rehearsal_* name; refusing";; esac
load_env_file
require_isolated   # the rehearsal server is loopback and is not the production database
info "run directory: $REAL_RUN"
info "restored database: $DB  (on the isolated rehearsal server)"
info "files: $(find "$REAL_RUN" -maxdepth 1 -type f | wc -l)"
if [ "$DRY" = 1 ]; then echo "DRY RUN: nothing was deleted."; exit 0; fi
use_target "$REHEARSAL_VAR" postgres
psql_rw -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" >/dev/null
[ -z "$(psql_ro -c "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ] || die "the restored database still exists"
for f in "$REAL_RUN/backup.dump" "$REAL_RUN/backup-counts.txt" "$REAL_RUN/restored-counts.txt" "$REAL_RUN/baseline-counts.txt"; do
  [ -f "$f" ] && { shred -u "$f" 2>/dev/null || rm -f "$f"; }
done
rm -rf -- "$REAL_RUN"
[ ! -e "$REAL_RUN" ] || die "the run directory still exists"
echo "CLEANED: restored database dropped, dump and counts shredded (best effort), run directory removed. Production and its backups were not touched."
