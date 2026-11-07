"""AUD-113: scripts/apply_migration_014.sh must be fail-closed, credential-safe and apply only the approved SQL. The refusals run everywhere; the end-to-end run
needs a disposable PostgreSQL on loopback (AUDLI_TEST_POSTGRES_URL) and the psql client. Production is never contacted."""
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'apply_migration_014.sh'
APPROVED = '451ca00a415119f4534c8c5b9a19aba7f66fcc67c6a9512288ab58d139cd0703'
REF = 'fopyknjekizcgrazisnq'
FAKE_PW = 'FAKEPW-do-not-leak-123'
pytestmark = pytest.mark.skipif(not shutil.which('bash') or not shutil.which('python3'), reason='needs bash')


def code(path):
    return '\n'.join(line for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#'))


def backup(tmp_path, project=True):
    d = tmp_path / 'pre014-test'
    d.mkdir()
    (d / 'audli-public.dump').write_text('dump\n')
    subprocess.run(['sha256sum', 'audli-public.dump'], cwd=d, check=True, stdout=(d / 'audli-public.dump.sha256').open('w'))
    manifest = 'ledger_listening=9,10,11,12  ledger_main=1,2,3,4,5,6,7,8  library_table=absent\n'
    if project:
        manifest += f'connection_kind=session-pooler  host=h  user=postgres.{REF}\n'
    (d / 'MANIFEST.txt').write_text(manifest)
    return d


def run(url, backup_dir, script=SCRIPT, **env):
    return subprocess.run(['bash', str(script), str(backup_dir)], env={**os.environ, 'AUDLI_OWNER_URL': url, **env}, capture_output=True, text=True, timeout=120)


def test_script_is_fail_closed_applies_only_the_approved_file_and_never_retries():
    text, c = SCRIPT.read_text(), code(SCRIPT)
    subprocess.run(['bash', '-n', str(SCRIPT)], check=True)
    assert 'set -euo pipefail' in c and "refusing to run with xtrace" in c and APPROVED in c
    assert c.count('psql -X -v ON_ERROR_STOP=1 -f "$SQL"') == 1 and 'postgres-014.sql' in c and 'postgres-015' not in text
    executable = [line for line in c.splitlines() if not line.lstrip().startswith(('echo', '#', '"!!'))]
    assert not any(w in ' '.join(executable) for w in ('DROP ', 'dropdb', 'pg_dump')), 'rollback is printed for a human, never executed'
    assert not any(re.match(r'\s*(while|until)\b|sleep|seq ', line) for line in c.splitlines()), 'a failed migration must not be retried'
    assert 'echo "$AUDLI_OWNER_URL"' not in c and 'unset AUDLI_OWNER_URL' in c and 'PGPASSWORD' in c


@pytest.mark.parametrize('url,needle', [
    (f'postgresql://postgres.{REF}:{FAKE_PW}@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres', 'TRANSACTION pooler'),
    (f'postgresql://postgres.otherref:{FAKE_PW}@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres', 'wrong project'),
    (f'postgresql://postgres:{FAKE_PW}@db.{REF}.supabase.co:5432/postgres', 'Session Pooler'),
    (f'postgresql://postgres:{FAKE_PW}@127.0.0.1:5432/postgres', 'loopback host refused'),
])
def test_wrong_endpoint_is_refused_before_any_connection_without_leaking_the_password(tmp_path, url, needle):
    r = run(url, backup(tmp_path))
    assert r.returncode == 1 and needle in r.stderr and 'Nothing was changed' in r.stderr
    assert FAKE_PW not in r.stdout + r.stderr


def test_disposable_flag_is_not_honoured_for_a_remote_host(tmp_path):
    r = run(f'postgresql://postgres.{REF}:{FAKE_PW}@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres', backup(tmp_path), AUDLI_APPLY_DISPOSABLE_TEST='1')
    assert r.returncode == 1 and 'only honoured for a loopback host' in r.stderr and FAKE_PW not in r.stdout + r.stderr


def test_changed_sql_or_changed_backup_stops_the_run(tmp_path):
    clone = tmp_path / 'repo'
    (clone / 'scripts').mkdir(parents=True)
    (clone / 'docs').mkdir()
    shutil.copy(SCRIPT, clone / 'scripts')
    (clone / 'docs' / 'postgres-014.sql').write_text((ROOT / 'docs' / 'postgres-014.sql').read_text() + '-- tampered\n')
    url = f'postgresql://postgres.{REF}:{FAKE_PW}@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
    r = run(url, backup(tmp_path), script=clone / 'scripts' / SCRIPT.name)
    assert r.returncode == 1 and 'not the approved' in r.stderr
    shutil.copy(ROOT / 'docs' / 'postgres-014.sql', clone / 'docs')
    d = backup(tmp_path / 'x') if (tmp_path / 'x').mkdir() is None else None
    (d / 'audli-public.dump').write_text('changed after the checksum\n')
    r = run(url, d, script=clone / 'scripts' / SCRIPT.name)
    assert r.returncode == 1 and 'backup checksum mismatch' in r.stderr


@pytest.mark.skipif(not os.environ.get('AUDLI_TEST_POSTGRES_URL') or not shutil.which('psql'), reason='needs a disposable PostgreSQL and psql')
def test_end_to_end_on_a_disposable_database_applies_once_verifies_and_refuses_a_second_run(tmp_path):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    from app.config import Settings
    from scripts.migrate_postgres import migrate
    admin = make_url(os.environ['AUDLI_TEST_POSTGRES_URL'])
    if admin.host not in ('127.0.0.1', 'localhost', '::1'):
        pytest.skip('the script only accepts a loopback test server')
    name = 'audli_apply_' + uuid4().hex
    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as db:
        for role in ('anon', 'authenticated', 'service_role', 'audli_api_runtime', 'audli_listening_worker', 'audli_listening_generator'):
            db.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url = admin.set(database=name).render_as_string(hide_password=False)
    owner_url = admin.set(drivername='postgresql', database=name).render_as_string(hide_password=False)
    settings = Settings(persistence='postgres', database_url=url.replace('postgresql://', 'postgresql+psycopg://', 1), learner_id='00000000-0000-0000-0000-000000000001')
    migrate(settings)
    with psycopg.connect(owner_url, autocommit=True) as db:
        db.execute((ROOT / 'docs' / 'postgres-008.sql').read_text().replace('%%', '%'))
        for n in ('009', '010', '011', '012'):
            db.execute((ROOT / 'docs' / f'postgres-{n}.sql').read_text())
    try:
        env = {'AUDLI_APPLY_DISPOSABLE_TEST': '1', 'AUDLI_TEST_SSLMODE': 'prefer'}
        major = subprocess.run(['psql', owner_url, '-qAt', '-c', "select current_setting('server_version_num')::int / 10000"], capture_output=True, text=True).stdout.strip()
        first = run(owner_url, backup(tmp_path, project=False), **env)
        if major != '17':
            assert first.returncode == 1 and 'expected 17' in first.stderr
            pytest.skip('the script pins PostgreSQL 17 (production); this cluster is %s' % major)
        assert first.returncode == 0, first.stderr + first.stdout
        assert 'RESULT: MIGRATION 014 APPLIED AND VERIFIED' in first.stdout and 'grants besides the owner = audli_api_runtime:SELECT' in first.stdout
        second = run(owner_url, tmp_path / 'pre014-test', **env)
        assert second.returncode == 1 and 'expected exactly 9,10,11,12' in second.stderr and 'Nothing was changed' in second.stderr
        assert admin.password is None or admin.password not in first.stdout + first.stderr + second.stdout + second.stderr
    finally:
        with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as db:
            db.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
