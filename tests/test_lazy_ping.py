"""AUD-87: the pool pre-ping is skipped for connections used moments ago (AUDLI_DB_LAZY_PING, default off)."""
import time

import pytest
from sqlalchemy import text

from app.config import Settings
from app.lazyping import install_lazy_ping
from app.main import create_app
from app.telemetry import aggregator
from conftest import ASGIClient, FakeProvider
from test_state_only_ready import make_repo  # noqa: F401  (fixture)


def postgres_only(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Pre-ping only matters on PostgreSQL.')


def client_for(repo, tmp_path, flag, seconds=30):
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path, latency_aggregates=True, db_lazy_ping=flag, db_lazy_ping_seconds=seconds, _env_file=None), provider, repo)
    return ASGIClient(app, provider)


def pings(client, requests=12):
    aggregator.reset()
    for _ in range(requests):
        assert client.get('/api/profile').status_code == 200
    stages = aggregator.snapshot()['stages']
    return stages.get('db.pool_ping', {}).get('count', 0), stages['db.checkout']['count']


def test_off_by_default():
    assert Settings.model_fields['db_lazy_ping'].default is False and Settings.model_fields['db_lazy_ping_seconds'].default == 30


def test_pings_every_checkout_off_and_almost_none_on(tmp_path, make_repo):
    repo = make_repo('x'); postgres_only(repo)
    off = pings(client_for(repo, tmp_path / 'off', False))
    repo2 = make_repo('y')
    on = pings(client_for(repo2, tmp_path / 'on', True))
    assert off[0] >= off[1] - 2 and off[1] >= 12              # one ping per checkout (the first checkouts may be new connections)
    assert on[0] <= 3 and on[1] >= 12                         # only the very first use of each connection


def test_a_connection_idle_longer_than_the_window_is_pinged_again(tmp_path, make_repo):
    repo = make_repo('x'); postgres_only(repo)
    client = client_for(repo, tmp_path, True, seconds=1)
    pings(client, 3)
    time.sleep(1.3)
    aggregator.reset()
    assert client.get('/api/profile').status_code == 200
    assert aggregator.snapshot()['stages']['db.pool_ping']['count'] >= 1


def test_a_connection_that_died_while_idle_is_replaced_by_the_ping(tmp_path, make_repo):
    repo = make_repo('x'); postgres_only(repo)
    client = client_for(repo, tmp_path, True, seconds=1)
    pings(client, 3)
    time.sleep(1.3)
    with repo.engine.connect() as admin:
        admin.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()'))
        admin.commit()
    assert [client.get('/api/profile').status_code for _ in range(3)] == [200, 200, 200]


def test_results_are_identical_and_installing_twice_is_harmless(tmp_path, make_repo):
    repo = make_repo('x'); postgres_only(repo)
    install_lazy_ping(repo.engine, 5); install_lazy_ping(repo.engine, 5)
    off = client_for(make_repo('z'), tmp_path / 'a', False).get('/api/profile').json()
    on = client_for(repo, tmp_path / 'b', True).get('/api/profile').json()
    assert on == off


def test_sqlite_is_untouched(tmp_path, make_repo):
    repo = make_repo('x')
    if repo.engine.dialect.name == 'postgresql':
        pytest.skip('SQLite only')
    assert client_for(repo, tmp_path, True).get('/api/profile').status_code == 200 and not getattr(repo.engine, '_audli_lazy_ping', False)
