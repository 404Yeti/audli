#!/usr/bin/env bash
# [LOCAL] Stores the PRODUCTION database URL for these scripts. It prompts with hidden input, so the URL is never typed on a
# command line, never shown, never in shell history and never pasted into chat. It only checks the URL's shape (no connection).
#   00-set-production-url.sh [--env-file FILE]
. "$(dirname "$0")/lib.sh"
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  *) die "unknown option $1";; esac; done
banner LOCAL "store the production database URL in a private file (no connection is made)"
need python3
echo "  Paste the Supabase session-pooler (or direct) connection string, then press Enter. Nothing is echoed."
read -rs -p "  $PROD_VAR: " AUDLI_NEW_VALUE; echo
export AUDLI_NEW_VALUE
check() { AUDLI_PROD_DATABASE_URL="$AUDLI_NEW_VALUE" python3 "$HERE/pgenv.py" "$PROD_VAR" "$@"; }
check >/dev/null || die "that is not a complete postgresql:// URL (nothing was stored)"
! check | grep -q '^export PGPORT=6543$' || die "port 6543 is the transaction pooler; use the session pooler (5432) or the direct connection (nothing was stored)"
env_file_set "$PROD_VAR"
LABEL="$(check --label)"; FP="$(check --fingerprint)"
unset AUDLI_NEW_VALUE
info "stored in ${ENV_FILE:-$ENV_FILE_DEFAULT} (mode 600)"; info "target: $LABEL  fingerprint: $FP"
echo "STORED. Remember the fingerprint; the apply step will ask for it."
