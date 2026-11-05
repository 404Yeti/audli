"""The in-container worker launch: a scrubbed environment, the worker's own database role, and a restart policy."""
import sys

from app.listening import sidecar

API_ENV = {
    'PATH': '/usr/bin', 'AUDLI_ENVIRONMENT': 'production', 'AUDLI_PERSISTENCE': 'postgres', 'AUDLI_DATABASE_URL': 'postgresql://api-owner-role',
    'AUDLI_LISTENING_WORKER_DATABASE_URL': 'postgresql://audli_listening_worker.ref:pw@h.example.com:5432/postgres?sslmode=require', 'OPENAI_API_KEY': 'sk-x', 'AUDLI_LISTENING_ENABLED': 'true',
    'AUDLI_LISTENING_S3_ACCESS_KEY': 'lesson-key', 'AUDLI_LISTENING_S3_SECRET_KEY': 'lesson-secret', 'AUDLI_LISTENING_SIGNING_SECRET': 'signing-secret-value',
    'AUDLI_LISTENING_OPS_TOKEN': 'ops-token-value-ops-token-value', 'AUDLI_LISTENING_RECORDINGS_BUCKET': 'rec', 'AUDLI_LISTENING_RECORDINGS_ACCESS_KEY': 'rk',
    'AUDLI_LISTENING_RECORDINGS_SECRET_KEY': 'rs', 'AUDLI_LISTENING_RECORDINGS_ENDPOINT': 'https://r2.example.com', 'ELEVENLABS_API_KEY': 'el-key',
    'AUDLI_ALLOWED_ORIGINS': 'https://app.audli.ai', 'AUDLI_LISTENING_ALLOWED_LEARNERS': 'x'}


def test_the_worker_gets_its_own_role_and_none_of_the_api_only_secrets():
    env = sidecar.worker_environment(API_ENV)
    assert env['AUDLI_DATABASE_URL'] == API_ENV['AUDLI_LISTENING_WORKER_DATABASE_URL'] and env['AUDLI_LISTENING_PROCESS'] == 'worker'
    for forbidden in ('AUDLI_LISTENING_S3_ACCESS_KEY', 'AUDLI_LISTENING_S3_SECRET_KEY', 'AUDLI_LISTENING_SIGNING_SECRET', 'AUDLI_LISTENING_OPS_TOKEN',
                      'ELEVENLABS_API_KEY', 'AUDLI_ALLOWED_ORIGINS', 'AUDLI_LISTENING_ALLOWED_LEARNERS'):
        assert forbidden not in env
    assert 'api-owner-role' not in ' '.join(env.values()) and env['OPENAI_API_KEY'] == 'sk-x' and env['AUDLI_LISTENING_RECORDINGS_BUCKET'] == 'rec'


def test_the_worker_settings_validate_with_this_environment():
    import pytest
    from app.config import Settings
    env = sidecar.worker_environment({**API_ENV, 'AUDLI_AUTH_MODE': 'supabase', 'AUDLI_SUPABASE_URL': 'https://abcdefghijklmnopqrst.supabase.co',
                                      'AUDLI_SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_x', 'AUDLI_LISTENING_PROVIDER': 'openai', 'AUDLI_LISTENING_STORAGE': 's3'})
    fields = {k[len('AUDLI_'):].lower() if k.startswith('AUDLI_') else k.lower(): v for k, v in env.items() if k not in ('PATH', 'PYTHONUNBUFFERED')}
    Settings(_env_file=None, **fields)


def test_restart_policy_backs_off_when_the_worker_dies_at_once():
    assert sidecar.next_pause(5, 1) == 10 and sidecar.next_pause(10, 1) == 20 and sidecar.next_pause(100, 1) == sidecar.MAX_PAUSE
    assert sidecar.next_pause(80, 300) == sidecar.MIN_PAUSE


def test_the_launcher_restarts_and_never_raises(capsys):
    calls, sleeps = [], []

    class Done:
        returncode = 3
    ticks = iter(range(0, 1000, 1))
    code = sidecar.main(API_ENV, run=lambda *a, **k: calls.append((a, k)) or Done(), sleep=sleeps.append, clock=lambda: next(ticks), rounds=3)
    assert code == 0 and len(calls) == 3 and sleeps == [10, 20, 40]
    assert calls[0][0][0][:3] == [sys.executable, '-m', 'app.listening.worker'] and calls[0][1]['env']['AUDLI_LISTENING_PROCESS'] == 'worker'
    assert sidecar.main({}, rounds=1) == 1
