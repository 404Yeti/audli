"""AUD-87: what the lazy ping risks, measured: a connection that dies INSIDE the idle window."""
import pytest
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from conftest import ASGIClient, FakeProvider
from test_state_only_ready import make_repo  # noqa: F401  (fixture)


def client_for(repo, tmp_path, flag=True):
    provider = FakeProvider()
    return ASGIClient(create_app(Settings(data_dir=tmp_path, db_lazy_ping=flag, _env_file=None), provider, repo), provider)


def kill_every_other_backend(repo):
    """From a separate connection, so the application's own pooled connections are the ones that die."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    admin = create_engine(repo.engine.url, poolclass=NullPool)
    with admin.connect() as connection:
        connection.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()'))
        connection.commit()
    admin.dispose()


def postgres_only(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')


def test_a_connection_killed_inside_the_window_fails_at_most_once_then_everything_recovers(make_repo, tmp_path):
    repo = make_repo('x'); postgres_only(repo)
    client = client_for(repo, tmp_path)
    assert [client.get('/api/profile').status_code for _ in range(4)] == [200] * 4        # connections are warm and unpinged
    kill_every_other_backend(repo)
    codes = [client.get('/api/profile').status_code for _ in range(5)]
    print('codes after losing every backend inside the window:', codes)
    assert codes.count(200) >= 4 and all(code in (200, 503) for code in codes), codes     # at most the first statement(s) of one pool slot fail
    assert codes[-3:] == [200, 200, 200]                                                    # and the pool has healed itself
    assert repo.engine.pool.checkedout() == 0                                               # nothing leaked


def test_the_same_event_with_the_flag_off_is_what_pre_ping_already_covers(make_repo, tmp_path):
    repo = make_repo('x'); postgres_only(repo)
    client = client_for(repo, tmp_path, flag=False)
    assert [client.get('/api/profile').status_code for _ in range(4)] == [200] * 4
    kill_every_other_backend(repo)
    assert [client.get('/api/profile').status_code for _ in range(5)] == [200] * 5          # the baseline never fails here


def test_a_write_interrupted_by_a_dead_connection_leaves_no_partial_state_and_the_next_request_works(make_repo, tmp_path, monkeypatch):
    repo = make_repo('x'); postgres_only(repo)
    client = client_for(repo, tmp_path)
    assert client.get('/api/profile').status_code == 200
    before = client.get('/api/profile').json()
    original = type(repo).save_profile

    def dying(self, profile):
        kill_every_other_backend(repo)                      # the connection dies before the write transaction starts
        return original(self, profile)
    monkeypatch.setattr(type(repo), 'save_profile', dying)
    body = {'name': 'Interrupted', 'goal': 'listen'}
    interrupted = client.put('/api/profile', json=body)
    monkeypatch.undo()
    assert interrupted.status_code in (200, 503)
    if interrupted.status_code == 503:
        assert client.get('/api/profile').json()['profile']['name'] == before['profile']['name']      # nothing half-written
    retry = client.put('/api/profile', json=body)
    assert retry.status_code == 200 and client.get('/api/profile').json()['profile']['name'] == 'Interrupted'
    assert repo.engine.pool.checkedout() == 0


def test_repeated_backend_loss_never_wedges_the_pool(make_repo, tmp_path):
    repo = make_repo('x'); postgres_only(repo)
    client = client_for(repo, tmp_path)
    for _ in range(4):
        assert client.get('/api/profile').status_code in (200, 503)
        kill_every_other_backend(repo)
    assert [client.get('/api/profile').status_code for _ in range(4)][-2:] == [200, 200]
    assert repo.engine.pool.checkedout() == 0
