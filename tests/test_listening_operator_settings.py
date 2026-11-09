"""Regression: the operator CLIs (curate, library) failed with
'Postgres requires AUDLI_DATABASE_URL; local mode also requires AUDLI_LEARNER_ID' although AUDLI_DATABASE_URL was set,
because Settings() defaults auth_mode to local and then demands the 1.x learner id. The URL below is a dummy: nothing connects."""
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.listening import curate, library
from app.listening.worker import operator_settings

DUMMY = 'postgresql://postgres.dummyref:dummy-pass@aws-0-dummy.pooler.supabase.com:5432/postgres'
ENV = {'AUDLI_PERSISTENCE': 'postgres', 'AUDLI_DATABASE_URL': DUMMY, 'AUDLI_LISTENING_ENABLED': 'true',
       'AUDLI_LISTENING_CURATOR_IDS': '00000000-0000-0000-0000-000000000001'}
CLEAR = ('AUDLI_LEARNER_ID', 'AUDLI_AUTH_MODE', 'AUDLI_ENVIRONMENT', 'AUDLI_SUPABASE_URL', 'AUDLI_SUPABASE_PUBLISHABLE_KEY')


@pytest.fixture
def env(monkeypatch):
    for name in CLEAR:
        monkeypatch.delenv(name, raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir('/')            # no stray .env
    return monkeypatch


def test_plain_settings_reproduce_the_reported_failure(env):
    with pytest.raises(ValidationError) as failure:
        Settings()
    assert 'local mode also requires AUDLI_LEARNER_ID' in str(failure.value)


def test_operator_settings_accept_the_same_environment(env):
    settings = operator_settings()
    assert settings.persistence == 'postgres' and settings.database_url.get_secret_value() == DUMMY


@pytest.mark.parametrize('module', [curate, library])
def test_both_operator_clis_get_past_settings(env, module):
    reached = []
    env.setattr('app.listening.worker.make_engine', lambda s: reached.append(s) or (_ for _ in ()).throw(RuntimeError('stop before any connection')))
    argv = ['plan', '--stage', 'pilot', '--curator', '00000000-0000-0000-0000-000000000001'] if module is curate else ['status']
    with pytest.raises(RuntimeError, match='stop before any connection'):
        module.main(argv)
    assert reached


def test_validation_is_not_weakened(env):
    env.setenv('AUDLI_DATABASE_URL', 'postgresql://nohost')
    with pytest.raises(ValidationError, match='complete Postgres URI'):
        operator_settings()
    env.setenv('AUDLI_DATABASE_URL', DUMMY)
    env.delenv('AUDLI_DATABASE_URL')
    with pytest.raises(ValidationError, match='requires AUDLI_DATABASE_URL'):
        operator_settings()


def test_supabase_mode_still_rejects_a_learner_id_and_production_rules_stand(env):
    env.setenv('AUDLI_AUTH_MODE', 'supabase')
    env.setenv('AUDLI_LEARNER_ID', '11111111-1111-1111-1111-111111111111')
    env.setenv('AUDLI_SUPABASE_URL', 'https://x.supabase.co')
    env.setenv('AUDLI_SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_x')
    with pytest.raises(ValidationError, match='Remove AUDLI_LEARNER_ID'):
        operator_settings()
    env.delenv('AUDLI_AUTH_MODE'); env.delenv('AUDLI_LEARNER_ID')
    env.setenv('AUDLI_ENVIRONMENT', 'production')
    with pytest.raises(ValidationError):                     # production still refuses local identity (an earlier production rule may fire first)
        operator_settings()


# ---- which condition rejects a database URL: the error now names it (never a value) -------------------------------------------------------
SECRET = 'S3cr3t-synthetic'
GOOD = f'postgresql://postgres.abcdefgh:{SECRET}@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require'


@pytest.mark.parametrize('url, environment, problem', [
    (GOOD + '&options=reference%3Dabc', 'development', 'options parameter'),
    (f'mysql://u:{SECRET}@h:5432/d', 'development', 'scheme'),
    (f'postgresql://postgres.abcdefgh:{SECRET}@aws-0-x.pooler.supabase.com:5432/', 'development', 'database'),
    ('postgresql://postgres.abcdefgh@aws-0-x.pooler.supabase.com:5432/postgres', 'development', 'password'),
])
def test_the_error_names_the_failing_condition_and_never_the_password(env, url, environment, problem):
    env.setenv('AUDLI_DATABASE_URL', url)
    env.setenv('AUDLI_ENVIRONMENT', environment)
    with pytest.raises(ValidationError) as failure:
        Settings(learner_id='11111111-1111-1111-1111-111111111111')
    text = str(failure.value)
    assert f'Problem: ' in text and problem in text and SECRET not in text


def test_a_good_url_passes_with_and_without_sslmode_when_not_production(env):
    env.setenv('AUDLI_DATABASE_URL', GOOD.replace('?sslmode=require', ''))
    assert operator_settings().persistence == 'postgres'
    env.setenv('AUDLI_DATABASE_URL', GOOD)
    assert operator_settings().persistence == 'postgres'
