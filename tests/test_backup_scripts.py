"""AUD-113: the manual pre-migration backup and restore-test scripts must stay read-only, credential-safe and separate from migration execution.
Behavior against a real server was exercised by hand against a disposable PostgreSQL 17 (see docs/LISTENING-LIBRARY-BACKUP-014.md); these tests pin the safety properties."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP, VERIFY, FINGERPRINT = (ROOT / 'scripts' / n for n in ('backup_pre_migration.sh', 'verify_restore_pre_migration.sh', 'pre_migration_fingerprint.sql'))
DOC = ROOT / 'docs' / 'LISTENING-LIBRARY-BACKUP-014.md'
pytestmark = pytest.mark.skipif(not shutil.which('bash'), reason='needs bash')
FAKE = 'FAKEPW-do-not-leak-123'


def code_lines(path):
    return [line for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]


def run(script, url, *args, extra=None):
    env = {**os.environ, 'AUDLI_OWNER_URL': url, **(extra or {})}
    return subprocess.run(['bash', str(script), *args], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize('script', [BACKUP, VERIFY])
def test_scripts_parse_and_refuse_xtrace(script):
    assert subprocess.run(['bash', '-n', str(script)]).returncode == 0
    assert script.read_bytes().count(b'\r') == 0, 'CRLF would break bash on Windows checkouts'
    assert re.search(r'case "\$-" in \*x\*\)', script.read_text()), 'xtrace would print credentials'
    assert not any(re.match(r'\s*set\s+-\w*x', line) for line in code_lines(script))


def test_no_credentials_or_urls_with_passwords_are_committed_anywhere_in_the_procedure():
    pattern = re.compile(r'postgres(?:ql)?://[^\s\'"<>$]+:[^\s\'"<>$@]+@')
    for path in (BACKUP, VERIFY, FINGERPRINT, DOC):
        assert not pattern.search(path.read_text()), f'{path.name} contains a connection string with a password'
    assert not re.search(r'\bPGPASSWORD=\S', BACKUP.read_text()) and 'echo "$AUDLI_OWNER_URL' not in BACKUP.read_text()


def test_ignore_rules_keep_backups_and_secrets_out_of_git():
    ignored = (ROOT / '.gitignore').read_text()
    for entry in ('*.dump', 'audli-backups/', 'pre014-*/', '.env'):
        assert entry in ignored.splitlines()
    assert '*.sh text eol=lf' in (ROOT / '.gitattributes').read_text()


def test_backup_is_read_only_and_never_applies_a_migration():
    code = '\n'.join(code_lines(BACKUP)) + '\n' + '\n'.join(code_lines(FINGERPRINT))
    assert not re.search(r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY)\b', code), 'the backup must only read'
    assert 'postgres-014' not in code and 'migrate_postgres' not in code and 'postgres-listening' not in code
    assert 'READ ONLY' in code and '--snapshot' in code


def test_restore_test_never_touches_production():
    code = '\n'.join(code_lines(VERIFY))
    assert 'AUDLI_OWNER_URL' not in code and 'PGHOST' not in code and 'pooler' not in code
    assert 'postgres:17' in code and 'rm -f' in code and 'trap cleanup EXIT' in code      # a disposable container that is always removed
    run_line = next(line for line in code.splitlines() if ' run -d ' in line)
    assert ' -p ' not in run_line, 'the scratch server must not publish a port'


def test_transaction_pooler_wrong_project_and_leaks_are_refused_before_any_connection(tmp_path):
    transaction = run(BACKUP, f'postgresql://postgres.fopyknjekizcgrazisnq:{FAKE}@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres', str(tmp_path))
    assert transaction.returncode == 2 and 'TRANSACTION pooler' in transaction.stderr
    wrong = run(BACKUP, f'postgresql://postgres.someotherproject:{FAKE}@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres', str(tmp_path))
    assert wrong.returncode == 2 and 'wrong project' in wrong.stderr
    nopass = run(BACKUP, 'postgresql://postgres.fopyknjekizcgrazisnq@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres', str(tmp_path))
    assert nopass.returncode == 2
    for result in (transaction, wrong, nopass):
        assert FAKE not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir()), 'nothing may be written before the checks pass'
    traced = subprocess.run(['bash', '-x', str(BACKUP)], env={**os.environ, 'AUDLI_OWNER_URL': f'postgresql://postgres:{FAKE}@x:5432/postgres'}, capture_output=True, text=True, timeout=60)
    assert traced.returncode == 2 and FAKE not in traced.stdout + traced.stderr


def test_fingerprint_ordering_does_not_depend_on_the_servers_collation():
    # Found 2026-11-07: production sorted the scope '2026-11' after '2026-11-07' while the scratch server sorted it first, so identical rows hashed differently.
    sql = '\n'.join(code_lines(FINGERPRINT))
    assert 'order by r::text collate "C"' in sql
    assert 'order by r::text)' not in sql


def test_restore_test_creates_every_role_named_by_default_privileges():
    # The dump carries ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin; without that role pg_restore reports errors (and exits 1).
    code = '\n'.join(code_lines(VERIFY))
    assert re.search(r'for role in supabase_admin ', code)
    assert 'RC == 0 && ERRS == 0' in code, 'the restore must still require exit 0 and zero error lines'
