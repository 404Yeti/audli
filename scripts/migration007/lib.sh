#!/usr/bin/env bash
# Shared setup for the migration 007 scripts (AUD-77). Source only. Reuses the reviewed migration 006 helpers and changes three things:
# the migration/rollback files, the private directory for runs, and the counts query (which also covers lesson_entitlements).
HERE7="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AUDLI_MIGRATION_FILE="${AUDLI_MIGRATION_FILE:-$HERE7/../../docs/postgres-007.sql}"
export AUDLI_MIGRATION_DIR="${AUDLI_MIGRATION_DIR:-$HOME/.config/audli/migration007}"
. "$HERE7/../migration006/lib.sh"
COUNTS="$HERE7/sql/counts.sql"
PREFLIGHT_SQL="$HERE7/sql/preflight.sql"
