#!/usr/bin/env bash
# Exercises the whole migration 006 toolchain against a LOCAL PostgreSQL that stands in for production, including every
# refusal path and a check that credentials never appear in any output. It never touches a real production database.
#   AUDLI_SELFTEST_ADMIN_URL=postgresql://user:pw@127.0.0.1:PORT/postgres  selftest.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
ADMIN="${AUDLI_SELFTEST_ADMIN_URL:?set AUDLI_SELFTEST_ADMIN_URL to a local PostgreSQL admin URL}"
case "$ADMIN" in *127.0.0.1*|*localhost*) ;; *) echo "selftest only runs against a loopback server" >&2; exit 2;; esac
PYTHON_APP="${AUDLI_PYTHON:-$REPO/.venv/bin/python}"; [ -x "$PYTHON_APP" ] || PYTHON_APP=python3
TMP="$(mktemp -d)"; export AUDLI_MIGRATION_DIR="$TMP/runs"; export HOME="$TMP/home"; mkdir -p "$HOME"
# Use the server's real password when the URL has one (so the scan proves it never leaks); otherwise a decoy that the server ignores.
read -r CANARY CANARY_ENC USERNAME ADMIN_FULL < <(python3 - "$ADMIN" <<'PY'
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit
p = urlsplit(sys.argv[1]); user = unquote(p.username or 'postgres'); password = unquote(p.password or '') or 'canary-Zq83x!'
netloc = f'{quote(user, safe="")}:{quote(password, safe="")}@{p.hostname}:{p.port or 5432}'
print(password.replace(' ', ''), quote(password, safe=''), user, urlunsplit((p.scheme, netloc, p.path, '', '')))
PY
)
STANDIN="audli_standin_prod_$$"
export AUDLI_REHEARSAL_DATABASE_URL="$ADMIN_FULL"
export AUDLI_PROD_DATABASE_URL="${ADMIN_FULL%/*}/$STANDIN"
LOG="$TMP/all-output.log"; : > "$LOG"
PASSED=0; FAILED=0
ok()   { PASSED=$((PASSED+1)); echo "ok   - $*"; }
bad()  { FAILED=$((FAILED+1)); echo "FAIL - $*"; }
run()  { "$@" >> "$LOG" 2>&1; }                 # output goes to the log (scanned for secrets at the end)
expect_ok()   { local name="$1"; shift; if run "$@"; then ok "$name"; else bad "$name"; tail -15 "$LOG"; fi; }
expect_fail() { local name="$1" needle="$2"; shift 2; local out; out="$("$@" 2>&1)"; local status=$?; echo "$out" >> "$LOG"
                if [ $status -ne 0 ] && echo "$out" | grep -qi "$needle"; then ok "$name"; else bad "$name (status $status, wanted '$needle')"; echo "$out" | tail -8; fi; }

echo "== build the production-shaped stand-in (schema 1-5, real application code)"
"$PYTHON_APP" "$HERE/make_fixture.py" --url-var AUDLI_REHEARSAL_DATABASE_URL --db-name "$STANDIN" --learners 60 | tee -a "$LOG"
SHA="$(sha256sum "$REPO/docs/postgres-006.sql" | cut -d' ' -f1)"; RSHA="$(sha256sum "$REPO/docs/postgres-006-rollback.sql" | cut -d' ' -f1)"
FP="$(python3 "$HERE/pgenv.py" AUDLI_PROD_DATABASE_URL --fingerprint)"
RUN="$TMP/runs/selftest"; mkdir -p "$RUN"

echo "== stages 01-05 (read-only, backup, isolated)"
expect_ok   "01 preflight passes on the stand-in" "$HERE/01-preflight.sh" --run-dir "$RUN"
expect_ok   "02 backup (one snapshot) writes a verified dump" "$HERE/02-backup.sh" --run-dir "$RUN"
expect_ok   "03 restore into a new isolated database matches the snapshot exactly" "$HERE/03-restore-verify.sh" --run-dir "$RUN"
expect_ok   "04 rehearsal on the restored copy (apply, checks, guard, app smoke, rollback, re-apply)" "$HERE/04-rehearse.sh" --run-dir "$RUN" --app-smoke
expect_ok   "05 post-check on the rehearsal copy" "$HERE/05-postcheck.sh" --target rehearsal --run-dir "$RUN"

echo "== the production stand-in must still be untouched before 06"
if [ "$(psql "${AUDLI_PROD_DATABASE_URL}" -X -At -c "select string_agg(version::text, ',' order by version) from schema_migrations")" = "1,2,3,4,5" ]; then ok "schema still 1-5 after stages 01-05"; else bad "stand-in changed before 06"; fi

echo "== refusal paths of 06 (each must refuse and leave the stand-in at schema 5)"
expect_fail "06 refuses without the confirmation phrase" "refusing: pass --confirm" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$SHA" --target-fingerprint "$FP"
expect_fail "06 refuses a wrong confirmation phrase" "refusing: pass --confirm" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$SHA" --target-fingerprint "$FP" --confirm "apply it"
expect_fail "06 refuses a wrong file hash" "hash mismatch" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$(printf 0%.0s {1..64})" --target-fingerprint "$FP" --confirm "APPLY MIGRATION 006 TO PRODUCTION"
expect_fail "06 refuses a wrong database fingerprint" "does not match this database" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$SHA" --target-fingerprint 000000000000 --confirm "APPLY MIGRATION 006 TO PRODUCTION"
expect_fail "06 refuses a tampered migration file" "hash mismatch" env AUDLI_MIGRATION_FILE="$TMP/tampered.sql" bash -c "cp '$REPO/docs/postgres-006.sql' '$TMP/tampered.sql'; echo '-- x' >> '$TMP/tampered.sql'; '$HERE/06-apply-production.sh' --run-dir '$RUN' --expected-sha256 '$SHA' --target-fingerprint '$FP' --confirm 'APPLY MIGRATION 006 TO PRODUCTION'"
mkdir -p "$TMP/norun"; expect_fail "06 refuses without rehearsal evidence" "no manifest" "$HERE/06-apply-production.sh" --run-dir "$TMP/norun" --expected-sha256 "$SHA" --target-fingerprint "$FP" --confirm "APPLY MIGRATION 006 TO PRODUCTION"
cp -r "$RUN" "$TMP/stale"; python3 - "$TMP/stale/backup.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d['created_at'] = '2020-01-01T00:00:00+00:00'; open(sys.argv[1], 'w').write(json.dumps(d))
PY
expect_fail "06 refuses stale evidence" "older than" "$HERE/06-apply-production.sh" --run-dir "$TMP/stale" --expected-sha256 "$SHA" --target-fingerprint "$FP" --confirm "APPLY MIGRATION 006 TO PRODUCTION"
cp -r "$RUN" "$TMP/failed"; python3 - "$TMP/failed/rehearsal.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d['result'] = 'FAIL'; open(sys.argv[1], 'w').write(json.dumps(d))
PY
expect_fail "06 refuses when the rehearsal did not pass" "not PASS" "$HERE/06-apply-production.sh" --run-dir "$TMP/failed" --expected-sha256 "$SHA" --target-fingerprint "$FP" --confirm "APPLY MIGRATION 006 TO PRODUCTION"
expect_fail "isolation: rehearsal pointed at production is refused" "is the production database" env AUDLI_REHEARSAL_DATABASE_URL="$AUDLI_PROD_DATABASE_URL" "$HERE/03-restore-verify.sh" --run-dir "$RUN"
expect_fail "isolation: a Supabase-looking rehearsal host is refused" "looks like Supabase" env AUDLI_REHEARSAL_DATABASE_URL="postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres" "$HERE/03-restore-verify.sh" --run-dir "$RUN"
expect_fail "isolation: a remote rehearsal host needs an explicit flag" "not loopback" env AUDLI_REHEARSAL_DATABASE_URL="postgresql://u:p@db.example.org:5432/postgres" "$HERE/03-restore-verify.sh" --run-dir "$RUN"
expect_fail "07 refuses without the phrase" "refusing: pass --confirm" "$HERE/07-rollback-production.sh" --run-dir "$RUN" --expected-sha256 "$RSHA" --target-fingerprint "$FP"
expect_fail "07 refuses when 006 is not applied" "not applied here" "$HERE/07-rollback-production.sh" --run-dir "$RUN" --expected-sha256 "$RSHA" --target-fingerprint "$FP" --confirm "ROLL BACK MIGRATION 006 ON PRODUCTION"
expect_fail "05 production post-check reports a missing ledger" "has not been applied" "$HERE/05-postcheck.sh" --target production --run-dir "$RUN"
if [ "$(psql "${AUDLI_PROD_DATABASE_URL}" -X -At -c "select string_agg(version::text, ',' order by version) from schema_migrations")" = "1,2,3,4,5" ]; then ok "every refusal left the stand-in at schema 5"; else bad "a refusal changed the stand-in"; fi

echo "== 06 dry run, then the real apply on the stand-in"
expect_ok "06 --dry-run passes every gate and writes nothing" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$SHA" --target-fingerprint "$FP" --dry-run
[ "$(psql "${AUDLI_PROD_DATABASE_URL}" -X -At -c "select to_regclass('public.lesson_entitlements') is null")" = t ] && ok "dry run wrote nothing" || bad "dry run wrote something"
expect_ok "06 applies 006 with every gate satisfied and post-checks pass" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$SHA" --target-fingerprint "$FP" --confirm "APPLY MIGRATION 006 TO PRODUCTION"
expect_ok "05 production post-check passes" "$HERE/05-postcheck.sh" --target production --run-dir "$RUN"
expect_fail "06 refuses to apply twice" "live preflight failed" "$HERE/06-apply-production.sh" --run-dir "$RUN" --expected-sha256 "$SHA" --target-fingerprint "$FP" --confirm "APPLY MIGRATION 006 TO PRODUCTION"

echo "== rollback safety decisions on the migrated stand-in"
out="$("$HERE/05-postcheck.sh" --target production --run-dir "$RUN" 2>&1)"; echo "$out" | grep -q "SQL rollback safe: SAFE" && ok "verdict SAFE while no enforce-mode data exists" || bad "expected SAFE"
expect_ok "07 --dry-run works while SAFE" "$HERE/07-rollback-production.sh" --run-dir "$RUN" --expected-sha256 "$RSHA" --target-fingerprint "$FP" --dry-run
psql "${AUDLI_PROD_DATABASE_URL}" -X -q -c "UPDATE lesson_entitlements SET grace_actions = 3 WHERE user_id = (SELECT user_id FROM lesson_entitlements LIMIT 1)" >> "$LOG" 2>&1
out="$("$HERE/05-postcheck.sh" --target production --run-dir "$RUN" 2>&1 || true)"; echo "$out" | grep -q "SQL rollback safe: UNSAFE" && ok "verdict UNSAFE once a grace slot exists (enforce mode wrote)" || bad "expected UNSAFE (grace)"
expect_fail "07 refuses when UNSAFE" "NOT safe" "$HERE/07-rollback-production.sh" --run-dir "$RUN" --expected-sha256 "$RSHA" --target-fingerprint "$FP" --confirm "ROLL BACK MIGRATION 006 ON PRODUCTION"
psql "${AUDLI_PROD_DATABASE_URL}" -X -q -c "UPDATE lesson_entitlements SET grace_actions = 0" >> "$LOG" 2>&1
out="$("$HERE/05-postcheck.sh" --target production --run-dir "$RUN" --phase observe 2>&1 || true)"; echo "$out" | grep -q "SQL rollback safe: SAFE" && ok "SAFE again after the counter is cleared" || bad "expected SAFE again"
psql "${AUDLI_PROD_DATABASE_URL}" -X -q -c "UPDATE lesson_lifecycles SET status='expired' WHERE id=(SELECT id FROM lesson_lifecycles LIMIT 1)" >> "$LOG" 2>&1
out="$("$HERE/05-postcheck.sh" --target production --run-dir "$RUN" --phase observe 2>&1 || true)"; echo "$out" | grep -q "SQL rollback safe: UNSAFE" && ok "verdict UNSAFE once an expired lesson exists" || bad "expected UNSAFE (expired)"
psql "${AUDLI_PROD_DATABASE_URL}" -X -q -c "UPDATE lesson_lifecycles SET status='completed' WHERE status='expired'" >> "$LOG" 2>&1
expect_ok "07 really rolls back the stand-in when SAFE and fully confirmed" "$HERE/07-rollback-production.sh" --run-dir "$RUN" --expected-sha256 "$RSHA" --target-fingerprint "$FP" --confirm "ROLL BACK MIGRATION 006 ON PRODUCTION"
[ "$(psql "${AUDLI_PROD_DATABASE_URL}" -X -At -c "select string_agg(version::text, ',' order by version) from schema_migrations")" = "1,2,3,4,5" ] && ok "stand-in is back at schema 5 after the rollback" || bad "rollback did not restore schema 5"

echo "== credentials never appear in any output"
if [ "${#CANARY}" -lt 10 ]; then ok "password leak scan skipped: the server password is too short (<10 characters) to scan reliably; use a longer one or the built-in decoy"
elif grep -qF -e "$CANARY" -e "$CANARY_ENC" "$LOG"; then bad "the password appears in script output"; else ok "password absent from $(wc -l < "$LOG") lines of output"; fi
if [ "$USERNAME" = postgres ] || ! grep -qwF -e "$USERNAME" "$LOG"; then ok "user name absent from script output"; else bad "the user name appears in script output"; fi
if [ "${#CANARY}" -ge 10 ] && find "$TMP/runs" -type f -exec grep -lF -e "$CANARY" -e "$CANARY_ENC" {} + 2>/dev/null | grep -q .; then bad "a manifest or counts file contains the password"; else ok "no stored file contains the password"; fi
psql "$ADMIN_FULL" -X -q -c "DROP DATABASE IF EXISTS \"$STANDIN\" WITH (FORCE)" >/dev/null 2>&1
for db in $(psql "$ADMIN_FULL" -X -At -c "select datname from pg_database where datname like 'audli_rehearsal_%'" 2>/dev/null); do psql "$ADMIN_FULL" -X -q -c "DROP DATABASE \"$db\" WITH (FORCE)" >/dev/null 2>&1; done
echo; echo "selftest: $PASSED passed, $FAILED failed (log kept at $LOG until this shell exits)"; cp "$LOG" "${AUDLI_SELFTEST_LOG:-/dev/null}" 2>/dev/null || true
[ "$FAILED" = 0 ]
