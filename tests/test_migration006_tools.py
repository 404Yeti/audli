"""Safety properties of the migration 006 tooling (scripts/migration006). None of the first group needs a database: every
refusal must happen before any connection is attempted, and credentials must never be printed or stored."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'scripts' / 'migration006'
CANARY = 'canary-Zq83x-value'
PROD = f'postgresql://deploy_user:{CANARY}@db.prodhost12345.supabase.co:5432/postgres?sslmode=require'
APPLY_PHRASE, ROLLBACK_PHRASE = 'APPLY MIGRATION 006 TO PRODUCTION', 'ROLL BACK MIGRATION 006 ON PRODUCTION'
SHA = hashlib.sha256((ROOT / 'docs' / 'postgres-006.sql').read_bytes()).hexdigest()
RSHA = hashlib.sha256((ROOT / 'docs' / 'postgres-006-rollback.sql').read_bytes()).hexdigest()


def run(script, *args, env=None, home=None):
    environment = {**os.environ, 'HOME': str(home or ROOT), 'AUDLI_PROD_DATABASE_URL': PROD,
                   'AUDLI_REHEARSAL_DATABASE_URL': 'postgresql://rehearsal:' + CANARY + '@127.0.0.1:5432/postgres', **(env or {})}
    environment.pop('ENV_FILE', None)
    result = subprocess.run(['bash', str(TOOLS / script), *args], capture_output=True, text=True, env=environment, timeout=60)
    return result, result.stdout + result.stderr


def fingerprint(url=PROD):
    return subprocess.run([sys.executable, str(TOOLS / 'pgenv.py'), 'AUDLI_PROD_DATABASE_URL', '--fingerprint'], capture_output=True, text=True,
                          env={**os.environ, 'AUDLI_PROD_DATABASE_URL': url}).stdout.strip()


@pytest.fixture
def run_dir(tmp_path):
    directory = tmp_path / 'run'; directory.mkdir()
    return directory


@pytest.mark.parametrize('script', sorted(path.name for path in TOOLS.glob('*.sh')))
def test_every_script_is_valid_bash_and_never_traces(script):
    assert subprocess.run(['bash', '-n', str(TOOLS / script)]).returncode == 0
    import re
    assert not re.search(r'^\s*(set\s+-\w*x|bash\s+-x)', (TOOLS / script).read_text(), re.M)  # a trace would print credentials


def test_every_stage_declares_what_it_may_do():
    banners = {'01-preflight.sh': 'READ-ONLY', '02-backup.sh': 'BACKUP', '03-restore-verify.sh': 'ISOLATED', '04-rehearse.sh': 'ISOLATED',
               '05-postcheck.sh': 'READ-ONLY', '06-apply-production.sh': 'PRODUCTION-MUTATING', '07-rollback-production.sh': 'PRODUCTION-MUTATING'}
    for name, category in banners.items():
        assert f'banner {category}' in (TOOLS / name).read_text(), name
    for name in ('01-preflight.sh', '02-backup.sh', '05-postcheck.sh'):  # these touch production only through read-only sessions
        assert 'psql_rw' not in (TOOLS / name).read_text(), name
    assert 'default_transaction_read_only=on' in (TOOLS / 'lib.sh').read_text()


def test_only_the_two_marked_scripts_can_write_to_production():
    writers = [path.name for path in TOOLS.glob('*.sh') if path.name not in ('lib.sh', 'selftest.sh')
               and 'psql_rw' in path.read_text() and 'use_target "$PROD_VAR"' in path.read_text()]
    assert sorted(writers) == ['06-apply-production.sh', '07-rollback-production.sh']


def test_credentials_are_never_printed_by_pgenv_label_or_fingerprint():
    env = {**os.environ, 'AUDLI_PROD_DATABASE_URL': PROD}
    for option in ('--label', '--fingerprint'):
        out = subprocess.run([sys.executable, str(TOOLS / 'pgenv.py'), 'AUDLI_PROD_DATABASE_URL', option], capture_output=True, text=True, env=env).stdout
        assert CANARY not in out and 'deploy_user' not in out and 'prodhost12345' not in out
    label = subprocess.run([sys.executable, str(TOOLS / 'pgenv.py'), 'AUDLI_PROD_DATABASE_URL', '--label'], capture_output=True, text=True, env=env).stdout
    assert 'db=postgres' in label and 'sup' in label  # enough to recognize the project without exposing it whole


def test_redaction_removes_password_user_host_and_addresses():
    env = {**os.environ, 'AUDLI_PROD_DATABASE_URL': PROD}
    text = f'connection to server at "db.prodhost12345.supabase.co" (10.20.30.40) failed: FATAL: password {CANARY} rejected for user "deploy_user"'
    out = subprocess.run([sys.executable, str(TOOLS / 'pgenv.py'), 'AUDLI_PROD_DATABASE_URL', '--redact'], input=text, capture_output=True, text=True, env=env).stdout
    assert CANARY not in out and 'deploy_user' not in out and 'prodhost12345' not in out and '10.20.30.40' not in out and '[password]' in out


def test_bad_url_message_does_not_echo_the_value():
    result = subprocess.run([sys.executable, str(TOOLS / 'pgenv.py'), 'AUDLI_PROD_DATABASE_URL'], capture_output=True, text=True,
                            env={**os.environ, 'AUDLI_PROD_DATABASE_URL': f'not-a-url-{CANARY}'})
    assert result.returncode != 0 and CANARY not in result.stdout + result.stderr


def write_manifest(directory, stage, **fields):
    data = {'result': 'PASS', 'created_at': '2999-01-01T00:00:00+00:00', 'source_fingerprint': 'fp', 'migration_sha256': 'sha', **fields}
    (directory / f'{stage}.json').write_text(json.dumps(data))


def require(directory, *stages, fp='fp', sha='sha', hours='24'):
    return subprocess.run([sys.executable, str(TOOLS / 'manifest.py'), 'require', str(directory), '--migration-sha', sha, '--fingerprint', fp,
                           '--max-age-hours', hours, *stages], capture_output=True, text=True)


def test_manifest_requires_pass_same_file_same_database_and_freshness(run_dir):
    write_manifest(run_dir, 'backup')
    assert require(run_dir, 'backup').returncode == 0
    assert 'no manifest' in require(run_dir, 'backup', 'restore').stderr
    assert 'different production database' in require(run_dir, 'backup', fp='other').stderr
    assert 'different migration file' in require(run_dir, 'backup', sha='other').stderr
    write_manifest(run_dir, 'backup', result='FAIL')
    assert 'not PASS' in require(run_dir, 'backup').stderr
    write_manifest(run_dir, 'backup', created_at='2020-01-01T00:00:00+00:00')
    assert 'older than' in require(run_dir, 'backup').stderr


def apply_args(run_dir, **override):
    values = {'--run-dir': str(run_dir), '--expected-sha256': SHA, '--target-fingerprint': fingerprint(), '--confirm': APPLY_PHRASE, **override}
    return [item for pair in values.items() if pair[1] is not None for item in pair]


def test_apply_refuses_before_connecting_in_every_unsafe_case(run_dir, tmp_path):
    cases = {
        'without the phrase': (dict(**{'--confirm': None}), 'refusing: pass --confirm'),
        'with a wrong phrase': ({'--confirm': 'yes please'}, 'refusing: pass --confirm'),
        'with the wrong file hash': ({'--expected-sha256': '0' * 64}, 'hash mismatch'),
        'with no file hash': ({'--expected-sha256': None}, 'hash mismatch'),
        'with the wrong database fingerprint': ({'--target-fingerprint': '0' * 12}, 'does not match this database'),
    }
    for label, (override, message) in cases.items():
        result, out = run('06-apply-production.sh', *apply_args(run_dir, **override), home=tmp_path)
        assert result.returncode != 0 and message in out, (label, out)
        assert CANARY not in out and 'deploy_user' not in out and 'could not translate host' not in out, label  # never reached the network
    result, out = run('06-apply-production.sh', *apply_args(run_dir), home=tmp_path)  # everything right, but no evidence
    assert result.returncode != 0 and 'no manifest' in out and 'rehearsal evidence is missing' in out and CANARY not in out


def test_apply_with_a_tampered_file_is_refused(run_dir, tmp_path):
    tampered = tmp_path / 'postgres-006.sql'
    tampered.write_text((ROOT / 'docs' / 'postgres-006.sql').read_text() + '\n-- edited\n')
    result, out = run('06-apply-production.sh', *apply_args(run_dir), env={'AUDLI_MIGRATION_FILE': str(tampered)}, home=tmp_path)
    assert result.returncode != 0 and 'hash mismatch' in out


def test_rollback_script_gates(run_dir, tmp_path):
    base = ['--run-dir', str(run_dir), '--target-fingerprint', fingerprint()]
    for args, message in (([*base, '--expected-sha256', RSHA], 'refusing: pass --confirm'),
                          ([*base, '--expected-sha256', '1' * 64, '--confirm', ROLLBACK_PHRASE], 'hash mismatch'),
                          (['--run-dir', str(run_dir), '--target-fingerprint', '0' * 12, '--expected-sha256', RSHA, '--confirm', ROLLBACK_PHRASE], 'does not match this database')):
        result, out = run('07-rollback-production.sh', *args, home=tmp_path)
        assert result.returncode != 0 and message in out and CANARY not in out, out


def test_rehearsal_must_be_isolated(run_dir, tmp_path):
    for url, message in ((PROD, 'is the production database'), ('postgresql://u:p@db.zzz.supabase.co:5432/postgres', 'looks like Supabase'),
                         ('postgresql://u:p@db.example.org:5432/postgres', 'not loopback')):
        result, out = run('03-restore-verify.sh', '--run-dir', str(run_dir), env={'AUDLI_REHEARSAL_DATABASE_URL': url}, home=tmp_path)
        assert result.returncode != 0 and message in out and CANARY not in out, out


def test_env_file_with_loose_permissions_is_refused(run_dir, tmp_path):
    env_file = tmp_path / 'creds.env'
    env_file.write_text(f"export AUDLI_PROD_DATABASE_URL='{PROD}'\n")
    env_file.chmod(0o644)
    result, out = run('01-preflight.sh', '--env-file', str(env_file), '--run-dir', str(run_dir), home=tmp_path)
    assert result.returncode != 0 and 'must have mode 600' in out and CANARY not in out


def test_shipped_rollback_file_matches_what_the_tests_use():
    text = (ROOT / 'docs' / 'postgres-006-rollback.sql').read_text()
    for needle in ('DROP TABLE lesson_entitlements', 'DROP FUNCTION lesson_entitlement_guard()', "DELETE FROM schema_migrations WHERE version = 6", 'BEGIN;', 'COMMIT;'):
        assert needle in text
    assert 'expired' not in text.split('ADD CONSTRAINT lesson_status_valid')[1].split(';')[0]


def local_postgres_admin_url():
    url = os.environ.get('AUDLI_TEST_POSTGRES_URL', '')
    if not url or not all(shutil.which(tool) for tool in ('psql', 'pg_dump', 'pg_restore')) or not any(host in url for host in ('127.0.0.1', 'localhost')):
        return None
    server = subprocess.run(['psql', url, '-At', '-c', 'show server_version_num'], capture_output=True, text=True).stdout.strip()
    client = subprocess.run(['pg_dump', '--version'], capture_output=True, text=True).stdout.split()[-1].split('.')[0]
    return url if server and int(client) >= int(server) // 10000 else None


@pytest.mark.skipif(local_postgres_admin_url() is None, reason='needs a loopback AUDLI_TEST_POSTGRES_URL and psql/pg_dump/pg_restore at least as new as the server')
def test_full_toolchain_selftest_on_a_local_postgres():
    result = subprocess.run(['bash', str(TOOLS / 'selftest.sh')], capture_output=True, text=True, timeout=900,
                            env={**os.environ, 'AUDLI_SELFTEST_ADMIN_URL': local_postgres_admin_url(), 'AUDLI_PYTHON': sys.executable})
    assert result.returncode == 0 and ', 0 failed' in result.stdout, result.stdout[-3000:]
