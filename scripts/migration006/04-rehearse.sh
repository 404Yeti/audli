#!/usr/bin/env bash
# [ISOLATED] Rehearses migration 006 on the RESTORED COPY of production data (never on production):
#   preflight on the copy -> hash check -> apply (timed, with the production lock/statement timeouts) -> post-checks and
#   baseline comparison -> guard-trigger behavior tests (inside rolled-back transactions) -> optional application smoke test ->
#   rollback rehearsal -> re-apply -> post-checks again.
#   04-rehearse.sh --run-dir DIR [--app-smoke] [--drop-after] [--env-file FILE] [--allow-remote-rehearsal]
. "$(dirname "$0")/lib.sh"
APP_SMOKE=0; DROP_AFTER=0; ALLOW_REMOTE_REHEARSAL=0
while [ $# -gt 0 ]; do case "$1" in
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  --run-dir) require_flag_value "$@"; RUN_DIR="$2"; shift 2;;
  --app-smoke) APP_SMOKE=1; shift;; --drop-after) DROP_AFTER=1; shift;;
  --allow-remote-rehearsal) ALLOW_REMOTE_REHEARSAL=1; shift;;
  *) die "unknown option $1";; esac; done
banner ISOLATED "migration 006 rehearsal on the restored copy (production is not contacted)"
need psql python3 sha256sum; require_run_dir; load_env_file
FP="$(python3 "$HERE/pgenv.py" "$PROD_VAR" --fingerprint)"
require_isolated
SHA="$(sha256_of "$MIGRATION_FILE")"; RSHA="$(sha256_of "$ROLLBACK_FILE")"
manifest require "$RUN_DIR" --migration-sha "$SHA" --fingerprint "$FP" --max-age-hours 24 backup restore || die "run 02 and 03 first (both must PASS for this migration file)"
DB="$(manifest get "$RUN_DIR/restore.json" rehearsal_database)"
use_target "$REHEARSAL_VAR" "$DB"; info "rehearsal database: $DB on $TARGET_LABEL"
info "migration sha256: $SHA"; info "rollback  sha256: $RSHA"
STEP=0; step() { STEP=$((STEP+1)); echo; echo "--- step $STEP: $* ---"; }
now() { date +%s.%N; }

step "preflight on the restored copy (it must look exactly like production before 006)"
PRE="$(psql_ro -f "$HERE/sql/preflight.sql")"; echo "$PRE" | print_lines
[ "$(echo "$PRE" | count_fails)" = 0 ] || die "the restored copy is not in the expected pre-006 shape"

step "apply migration 006 (lock_timeout 5s, statement_timeout 30s, as in production)"
T0="$(now)"; psql_apply -f "$MIGRATION_FILE" >/dev/null
SECONDS_TAKEN="$(python3 -c "print(round($(now) - $T0, 3))")"; info "applied in ${SECONDS_TAKEN}s"

step "post-checks and baseline comparison"
POST="$("$HERE/05-postcheck.sh" --target rehearsal --run-dir "$RUN_DIR" --phase migration 2>&1)" || { echo "$POST"; die "post-checks failed on the rehearsal copy"; }
echo "$POST" | grep -E '^(  PASS|  FAIL|POSTCHECK|SAFE|UNSAFE)' | print_lines

step "guard trigger behavior (every attempt runs inside a transaction that is rolled back)"
GUARD="$(psql -X -q -At -v ON_ERROR_STOP=0 2>&1 <<'SQL' || true
BEGIN;
INSERT INTO users(id, created_at) VALUES ('00000000-0000-4000-8000-0000000000aa', now());
INSERT INTO lesson_entitlements(user_id, last_started_at, next_eligible_at, last_lesson_id, voided_at, first_speech_failures, grace_actions, updated_at)
  VALUES ('00000000-0000-4000-8000-0000000000aa', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', NULL, NULL, 0, 0, now());
SAVEPOINT t1; UPDATE lesson_entitlements SET last_started_at='2026-01-01T05:00:00Z', next_eligible_at='2026-01-02T05:00:00Z' WHERE user_id='00000000-0000-4000-8000-0000000000aa'; ROLLBACK TO t1;
SAVEPOINT t2; UPDATE lesson_entitlements SET next_eligible_at = next_eligible_at + interval '1 hour' WHERE user_id='00000000-0000-4000-8000-0000000000aa'; ROLLBACK TO t2;
SAVEPOINT t3; UPDATE lesson_entitlements SET last_started_at='2026-01-02T00:00:00Z', next_eligible_at='2026-01-02T02:00:00Z' WHERE user_id='00000000-0000-4000-8000-0000000000aa'; ROLLBACK TO t3;
SAVEPOINT t4; UPDATE lesson_entitlements SET grace_actions = 26 WHERE user_id='00000000-0000-4000-8000-0000000000aa'; ROLLBACK TO t4;
SAVEPOINT t5; UPDATE lesson_entitlements SET grace_actions = grace_actions + 1 WHERE user_id='00000000-0000-4000-8000-0000000000aa'; SELECT 'ALLOWED grace slot'; ROLLBACK TO t5;
SAVEPOINT t6; UPDATE lesson_entitlements SET last_started_at='2026-01-02T00:00:00Z', next_eligible_at='2026-01-03T00:00:00Z' WHERE user_id='00000000-0000-4000-8000-0000000000aa'; SELECT 'ALLOWED genuine next window'; ROLLBACK TO t6;
SAVEPOINT t7; UPDATE lesson_entitlements SET next_eligible_at = last_started_at, voided_at = now() WHERE user_id='00000000-0000-4000-8000-0000000000aa'; SELECT 'ALLOWED void'; ROLLBACK TO t7;
ROLLBACK;
SQL
)"
GUARD="$(echo "$GUARD" | python3 "$HERE/pgenv.py" "$REHEARSAL_VAR" --redact)"
for needle in lesson_cooldown_active lesson_window_extension_refused lesson_window_invalid entitlement_grace_bounded \
              'ALLOWED grace slot' 'ALLOWED genuine next window' 'ALLOWED void'; do
  echo "$GUARD" | grep -q "$needle" || { echo "$GUARD" >&2; die "guard behavior check failed: expected '$needle'"; }
  info "ok: $needle"
done

if [ "$APP_SMOKE" = 1 ]; then
  step "application smoke test against the restored data (off and observe mode, read-only calls)"
  AUDLI_SMOKE_DATABASE_NAME="$DB" "$PYTHON_APP" "$HERE/app_smoke.py" --url-var "$REHEARSAL_VAR" --db-name "$DB" 2> >(redact >&2) | print_lines
fi

step "rollback rehearsal: apply the shipped rollback file and prove the copy is back to the pre-006 shape"
psql_apply -f "$ROLLBACK_FILE" >/dev/null
BACK="$(psql_ro -f "$HERE/sql/preflight.sql")"; [ "$(echo "$BACK" | count_fails)" = 0 ] || { echo "$BACK" >&2; die "after the rollback the schema is not back to the pre-006 shape"; }
psql_ro -f "$HERE/sql/counts.sql" > "$RUN_DIR/after-rollback-counts.txt"
diff -q "$RUN_DIR/backup-counts.txt" "$RUN_DIR/after-rollback-counts.txt" >/dev/null || die "the rollback changed data (compare backup-counts.txt and after-rollback-counts.txt)"
info "rollback restored the pre-006 shape and every count, status and checksum equals the dump snapshot"

step "re-apply 006 after the rollback (repeatability) and re-run the post-checks"
psql_apply -f "$MIGRATION_FILE" >/dev/null
"$HERE/05-postcheck.sh" --target rehearsal --run-dir "$RUN_DIR" --phase migration >/dev/null 2>&1 || die "post-checks failed after re-applying"
info "re-applied and verified"

manifest write "$RUN_DIR/rehearsal.json" result=PASS source_fingerprint="$FP" migration_sha256="$SHA" rollback_sha256="$RSHA" \
  apply_seconds="$SECONDS_TAKEN" rehearsal_database="$DB" app_smoke="$APP_SMOKE"
if [ "$DROP_AFTER" = 1 ]; then use_target "$REHEARSAL_VAR" postgres; psql_rw -c "DROP DATABASE \"$DB\" WITH (FORCE)" >/dev/null; info "dropped $DB"; fi
echo; echo "REHEARSAL PASS: migration ${SECONDS_TAKEN}s, post-checks, guard behavior, rollback and re-apply verified on restored production data."
