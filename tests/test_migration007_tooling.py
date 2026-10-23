"""The 007 backup and rehearsal tooling can read production but can never change it, apply a migration or roll one back.

These are structural guarantees checked on the scripts themselves (no database needed), plus the refusals of the rehearsal driver.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'scripts' / 'migration007'
SQL_FILES = sorted((TOOLS / 'sql').glob('*.sql'))
PRODUCTION_SCRIPTS = ('01-preflight.sh', '02-backup.sh')   # these two address the production database (read only)


def code(path):
    """The script without comments, so documentation cannot hide or fake a command."""
    return '\n'.join(line for line in path.read_text().splitlines() if not line.lstrip().startswith('#'))


def test_the_tools_directory_has_no_apply_or_rollback_script():
    names = {p.name for p in TOOLS.iterdir()}
    assert not [n for n in names if re.search(r'apply|rollback|migrate', n, re.I) and n != 'rehearse.py'], names
    assert {'01-preflight.sh', '02-backup.sh', '03-restore-verify.sh', '04-cleanup.sh', 'lib.sh', 'rehearse.py'} <= names


@pytest.mark.parametrize('script', sorted(p.name for p in TOOLS.glob('*.sh') if p.name != '04-cleanup.sh'))
def test_no_script_can_apply_or_roll_back_a_migration_or_write_to_production(script):
    text = code(TOOLS / script)
    for forbidden in ('psql_apply', 'PROD_WRITE_OK', 'ROLLBACK_FILE', '06-apply', '07-rollback', 'docs/postgres-007-rollback', 'DROP TABLE', 'DROP DATABASE',
                      'INSERT INTO', 'UPDATE ', 'DELETE FROM', 'TRUNCATE', 'ALTER TABLE'):
        assert forbidden not in text, f'{script} contains {forbidden}'
    assert not re.search(r'(psql\w*|\bpsql)\b[^\n]*-f\s+"?\$(\{)?MIGRATION_FILE', text), f'{script} would execute the migration file'
    assert 'set -x' not in text and 'xtrace' not in text   # credentials live in the environment


@pytest.mark.parametrize('script', PRODUCTION_SCRIPTS)
def test_scripts_that_address_production_open_read_only_sessions_only(script):
    text = code(TOOLS / script)
    assert 'psql_rw' not in text and 'psql_apply' not in text
    assert 'psql_ro' in text or 'READ ONLY' in text
    if script == '02-backup.sh':
        assert 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY' in text and 'default_transaction_read_only = on' in text
        assert 'pg_export_snapshot' in text and '--snapshot=' in text   # one snapshot for the dump AND the counts and checksums
        assert text.count('"$COUNTS"') == 1 and '$HERE/sql/counts.sql' not in text   # the 007 counts query, not the 006 one
    else:
        assert text.count('"$COUNTS"') == 1 and '"$PREFLIGHT_SQL"' in text


def test_only_the_restore_script_writes_and_only_to_the_isolated_rehearsal_server():
    text = code(TOOLS / '03-restore-verify.sh')
    assert 'require_isolated' in text and text.index('require_isolated') < text.index('psql_rw')
    assert text.index('use_target "$REHEARSAL_VAR"') < text.index('psql_rw')
    assert 'use_target "$PROD_VAR"' not in text                      # production is only ever fingerprinted here, never contacted
    # and the shared helper refuses a write session on the production fingerprint
    helper = (ROOT / 'scripts' / 'migration006' / 'lib.sh').read_text()
    assert 'refusing a write session against the production database' in helper


@pytest.mark.parametrize('path', SQL_FILES, ids=lambda p: p.name)
def test_the_sql_the_tools_run_is_read_only(path):
    statements = re.sub(r'--[^\n]*', '', path.read_text())
    assert not re.search(r'\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|LOCK)\b', statements, re.I), path.name
    assert re.search(r'\bSELECT\b', statements, re.I)
    allowed_set = re.findall(r'\bSET\s+(\w+(?:\s+\w+)?)', statements, re.I)
    assert all(s.upper() in ('TIME ZONE',) for s in allowed_set), allowed_set


def test_the_counts_query_covers_every_table_and_the_entitlement_checksum():
    sql = (TOOLS / 'sql' / 'counts.sql').read_text()
    assert "table_name <> 'lesson_entitlements'" not in sql          # 006's baseline excluded it; 007's must not
    assert 'entitlement_rows_md5' in sql and 'lesson_rows_md5' in sql and 'schema_versions' in sql
    assert "SET TIME ZONE 'UTC'" in sql                              # identical timestamp text on any server


def run_rehearsal(env):
    return subprocess.run([sys.executable, str(TOOLS / 'rehearse.py')], env={'PATH': '/usr/bin:/bin', **env}, capture_output=True, text=True, timeout=60, cwd=ROOT)


@pytest.mark.parametrize('variable', ['AUDLI_PROD_DATABASE_URL', 'AUDLI_DATABASE_URL', 'DATABASE_URL', 'SUPABASE_ACCESS_TOKEN', 'SUPABASE_URL'])
def test_the_rehearsal_refuses_to_start_when_a_production_variable_is_set(variable):
    done = run_rehearsal({variable: 'postgresql://example/x', 'AUDLI_REHEARSAL_DATABASE_URL': 'postgresql://u:p@127.0.0.1:1/postgres', 'PYTHONPATH': str(ROOT)})
    assert done.returncode != 0 and 'REFUSED' in (done.stdout + done.stderr) and 'example' not in (done.stdout + done.stderr)


@pytest.mark.parametrize('url', ['postgresql://u:p@db.internal.example:5432/postgres', 'postgresql://u:p@aws-0.pooler.supabase.com:5432/postgres'])
def test_the_rehearsal_refuses_a_non_loopback_or_supabase_server(url):
    done = run_rehearsal({'AUDLI_REHEARSAL_DATABASE_URL': url, 'PYTHONPATH': str(ROOT)})
    out = done.stdout + done.stderr
    assert done.returncode != 0 and 'REFUSED' in out and 'example' not in out.split('REFUSED')[1] and ':p@' not in out   # and it never echoes the URL


def test_the_scrubber_masks_identifiers_and_addresses():
    sys.path.insert(0, str(ROOT))
    import importlib.util
    spec = importlib.util.spec_from_file_location('rehearse007', TOOLS / 'rehearse.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.scrub('Key (user_id)=(3f2b1c9e-1a2b-4c3d-8e9f-0123456789ab) exists for maya.lee+x@example.com ' + 'x' * 400)
    assert '3f2b1c9e' not in text and 'maya' not in text and '<id>' in text and '<email>' in text and len(text) < 300


def test_cleanup_can_only_delete_the_run_directory_and_the_restored_database_after_verification():
    text = code(TOOLS / '04-cleanup.sh')
    for forbidden in ('use_target "$PROD_VAR"', 'psql_apply', 'PROD_WRITE_OK', 'backups/', 'migration006/', 'rm -rf /', 'rm -rf ~', 'rm -rf "$ROOT"', 'rm -rf "$HOME'):
        assert forbidden not in text, forbidden
    assert text.count('rm -rf') == 1 and 'rm -rf -- "$REAL_RUN"' in text                      # the only recursive delete is the validated run directory
    assert text.count('DROP DATABASE') == 1                                                    # the only database ever dropped is the one in the restore manifest
    gates = ['direct child of', 'UTC timestamp', 'PASSED restore manifest', 'PASSED rehearsal report', '--reported', 'confirmation phrase', 'audli_rehearsal_', 'require_isolated']
    positions = [text.index(g) if g in text else -1 for g in gates]
    assert -1 not in positions, dict(zip(gates, positions))
    assert max(positions) < text.index('DROP DATABASE') and max(positions) < text.index('rm -rf')   # every gate runs before anything is deleted
    assert text.index('use_target "$REHEARSAL_VAR"') < text.index('DROP DATABASE')
    assert 'PHRASE_CLEANUP=\'DELETE RESTORED PRODUCTION COPY\'' in text
