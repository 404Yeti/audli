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


def run_start(tmp_path, **env):
    """Run scripts/start.sh with stub `python` and `uvicorn`; report whether the worker sidecar was launched."""
    import os, subprocess, stat
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    for name, body in (('python', 'echo "sidecar $*" >> "$LOG"'), ('uvicorn', 'echo "api" >> "$LOG"')):
        path = bin_dir / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / 'log'
    log.write_text('')
    full = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'LOG': str(log), **env}
    subprocess.run(['sh', 'scripts/start.sh'], env=full, check=True, capture_output=True, timeout=20)
    import time; time.sleep(.3)                                       # the sidecar is a background process
    return log.read_text()


def test_start_script_launches_the_sidecar_for_every_truthy_spelling_the_api_accepts(tmp_path):
    for spelling in ('true', 'True', 'TRUE', '1', 'yes', 'on'):
        out = run_start(tmp_path, AUDLI_LISTENING_ENABLED=spelling, AUDLI_LISTENING_WORKER_DATABASE_URL='postgresql://x')
        assert 'sidecar -m app.listening.sidecar' in out and 'api' in out, spelling


def test_start_script_never_launches_the_sidecar_when_off_or_without_a_worker_url_and_always_starts_the_api(tmp_path):
    for env in ({}, {'AUDLI_LISTENING_ENABLED': 'false', 'AUDLI_LISTENING_WORKER_DATABASE_URL': 'postgresql://x'},
                {'AUDLI_LISTENING_ENABLED': 'true'}):
        out = run_start(tmp_path, **env)
        assert 'sidecar' not in out and 'api' in out, env


GENERATOR_ENV = {**API_ENV, 'AUDLI_LISTENING_GENERATOR_DATABASE_URL': 'postgresql://audli_listening_generator.ref:pw@h.example.com:5432/postgres?sslmode=require',
                 'AUDLI_LISTENING_GENERATION_ENABLED': 'true', 'AUDLI_LISTENING_S3_ENDPOINT': 'https://l.example.com', 'AUDLI_LISTENING_S3_BUCKET': 'lessons',
                 'AUDLI_LISTENING_GEN_MONTHLY_CREDITS': '100000'}


def test_the_generator_gets_lesson_write_and_speech_credentials_and_never_the_recordings_signing_or_ops_secrets():
    env = sidecar.worker_environment(GENERATOR_ENV, 'generator')
    assert env['AUDLI_DATABASE_URL'] == GENERATOR_ENV['AUDLI_LISTENING_GENERATOR_DATABASE_URL'] and env['AUDLI_LISTENING_PROCESS'] == 'generator'
    for needed in ('AUDLI_LISTENING_S3_ACCESS_KEY', 'AUDLI_LISTENING_S3_SECRET_KEY', 'ELEVENLABS_API_KEY', 'OPENAI_API_KEY', 'AUDLI_LISTENING_GEN_MONTHLY_CREDITS'):
        assert needed in env
    for forbidden in ('AUDLI_LISTENING_RECORDINGS_ACCESS_KEY', 'AUDLI_LISTENING_RECORDINGS_SECRET_KEY', 'AUDLI_LISTENING_RECORDINGS_BUCKET', 'AUDLI_LISTENING_SIGNING_SECRET',
                      'AUDLI_LISTENING_OPS_TOKEN', 'AUDLI_LISTENING_WORKER_DATABASE_URL', 'AUDLI_ALLOWED_ORIGINS'):
        assert forbidden not in env
    assert 'api-owner-role' not in ' '.join(env.values())


def test_the_spoken_worker_never_receives_the_generators_credentials_or_database_role():
    env = sidecar.worker_environment(GENERATOR_ENV, 'worker')
    assert 'ELEVENLABS_API_KEY' not in env and 'AUDLI_LISTENING_GENERATOR_DATABASE_URL' not in env and 'AUDLI_LISTENING_S3_ACCESS_KEY' not in env
    assert env['AUDLI_DATABASE_URL'] == GENERATOR_ENV['AUDLI_LISTENING_WORKER_DATABASE_URL']


def test_the_generator_settings_validate_with_this_environment():
    import pytest
    from app.config import Settings
    env = sidecar.worker_environment({**GENERATOR_ENV, 'AUDLI_AUTH_MODE': 'supabase', 'AUDLI_SUPABASE_URL': 'https://abcdefghijklmnopqrst.supabase.co',
                                      'AUDLI_SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_x', 'AUDLI_LISTENING_PROVIDER': 'openai', 'AUDLI_LISTENING_STORAGE': 's3'}, 'generator')
    fields = {k[len('AUDLI_'):].lower() if k.startswith('AUDLI_') else k.lower(): v for k, v in env.items() if k not in ('PATH', 'PYTHONUNBUFFERED')}
    s = Settings(_env_file=None, **fields)
    assert s.listening_process == 'generator' and s.listening_gen_speech_model == 'eleven_flash_v2_5'
    with pytest.raises(Exception, match='recordings credentials'):
        Settings(_env_file=None, **{**fields, 'listening_recordings_access_key': 'rk', 'listening_recordings_secret_key': 'rs'})
    with pytest.raises(Exception, match='ELEVENLABS_API_KEY'):
        Settings(_env_file=None, **{k: v for k, v in fields.items() if k != 'elevenlabs_api_key'})


def test_the_generator_launcher_uses_its_own_url_and_module(capsys):
    calls = []
    sidecar.main(GENERATOR_ENV, run=lambda cmd, env, check: calls.append((cmd, env['AUDLI_LISTENING_PROCESS'])) or type('R', (), {'returncode': 1})(),
                 sleep=lambda s: None, rounds=1, role='generator')
    assert calls == [([sys.executable, '-m', 'app.listening.generator'], 'generator')]
    assert sidecar.main({}, rounds=1, role='generator') == 1 and 'AUDLI_LISTENING_GENERATOR_DATABASE_URL is not set' in capsys.readouterr().err


def test_start_script_starts_the_generator_only_with_its_flag_and_database_url(tmp_path):
    base = {'AUDLI_LISTENING_ENABLED': 'true', 'AUDLI_LISTENING_WORKER_DATABASE_URL': 'postgresql://x'}
    out = run_start(tmp_path, **base)
    assert 'sidecar -m app.listening.sidecar\n' in out + '\n' and 'generator' not in out
    out = run_start(tmp_path, **base, AUDLI_LISTENING_GENERATION_ENABLED='true', AUDLI_LISTENING_GENERATOR_DATABASE_URL='postgresql://y')
    assert 'sidecar -m app.listening.sidecar generator' in out and 'api' in out
    out = run_start(tmp_path, **base, AUDLI_LISTENING_GENERATION_ENABLED='true')
    assert 'generator' not in out and 'api' in out
