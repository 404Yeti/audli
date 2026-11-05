"""While Audli 2.0 is limited to named test accounts, every other signed-in learner gets a plain 404 from every learner route, nothing is created for
them, and everything else (1.x, signed media, the monitor endpoint) behaves as before."""
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.listening import schema as l
from app.main import create_app
from conftest import ASGIClient, FakeProvider
from test_auth import AccountClient, USER_A, USER_B, auth_response, settings_for
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_library import prepare_all


@pytest.fixture
def stack(make_repository, tmp_path):
    repo = make_repository(None)
    prepare_all(repo, tmp_path)

    def build(**settings):
        app = create_app(settings_for(tmp_path, listening_enabled=True, **settings), FakeProvider(), repository=repo,
                         auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
        return AccountClient(app, None, 'valid-a'), AccountClient(app, None, 'valid-b')
    return build, repo


def lesson_id(client):
    return client.get('/api/listening/lessons').json()[0]['lesson_id']


def test_only_the_named_account_can_use_the_2_0_routes(stack):
    build, repo = stack
    a, b = build(listening_allowed_learners=USER_A)
    assert a.get('/api/listening/lessons').status_code == 200
    first = lesson_id(a)
    view = a.post(f'/api/listening/lessons/{first}/attempts', json={'client_request_id': uuid4().hex}).json()
    attempt = view['attempt']['id']
    exercise = view['exercises'][0]['id']
    for call in (lambda: b.get('/api/listening/lessons'), lambda: b.get('/api/listening/progress'), lambda: b.get(f'/api/listening/lessons/{first}/attempts/current'),
                 lambda: b.post(f'/api/listening/lessons/{first}/attempts', json={'client_request_id': uuid4().hex}), lambda: b.get(f'/api/listening/attempts/{attempt}'),
                 lambda: b.post(f'/api/listening/attempts/{attempt}/answers', json={'exercise_id': exercise, 'option_id': 'a'}),
                 lambda: b.get(f'/api/listening/attempts/{attempt}/audio'), lambda: b.post(f'/api/listening/attempts/{attempt}/exercises/{exercise}/skip'),
                 lambda: b.post(f'/api/listening/attempts/{attempt}/exercises/{exercise}/retry'),
                 lambda: b.post(f'/api/listening/attempts/{attempt}/exercises/{exercise}/recording', params={'upload_id': uuid4().hex}, content=b'x' * 400,
                                headers={'content-type': 'audio/wav'})):
        response = call()
        assert response.status_code == 404 and response.json() == {'detail': 'Not found.'}
    with repo.engine.connect() as db:                                      # the refused account left no trace
        assert db.execute(select(func.count()).select_from(l.attempts)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(l.spoken)).scalar_one() == 0


def test_with_no_allow_list_every_signed_in_learner_is_served_as_before(stack):
    build, _ = stack
    a, b = build()
    assert a.get('/api/listening/lessons').status_code == 200 and b.get('/api/listening/lessons').status_code == 200


def test_several_named_accounts_and_case_do_not_matter(stack):
    build, _ = stack
    a, b = build(listening_allowed_learners=f' {USER_B.upper()} , {USER_A} ')
    assert a.get('/api/listening/lessons').status_code == 200 and b.get('/api/listening/lessons').status_code == 200


def test_1x_and_unauthenticated_behaviour_are_unchanged_by_the_allow_list(stack):
    build, _ = stack
    a, b = build(listening_allowed_learners=USER_A)
    anonymous = ASGIClient(a.app, None)
    assert anonymous.get('/api/listening/lessons').status_code == 401                # still "sign in", not "not found"
    assert b.get('/api/health').status_code == 200 and b.get('/api/auth/config').status_code == 200


@pytest.mark.parametrize('value', ['not-a-uuid', 'a,b', f'{USER_A},oops', '12345'])
def test_a_malformed_allow_list_is_refused_at_startup(value):
    with pytest.raises(Exception, match='ALLOWED_LEARNERS'):
        Settings(listening_allowed_learners=value, _env_file=None)
    assert Settings(listening_allowed_learners=f'{USER_A}', _env_file=None).listening_allowed_set == {USER_A.lower()}


def test_a_refused_account_is_logged_once_by_prefix_only_and_startup_says_what_is_configured(stack, caplog):
    import logging
    caplog.set_level(logging.WARNING, logger='audli.listening')
    build, _ = stack
    a, b = build(listening_allowed_learners=USER_A)
    assert any('1 entries' in r.getMessage() and USER_A[:8] in r.getMessage() and USER_A not in r.getMessage() for r in caplog.records)
    for _ in range(3):
        assert b.get('/api/listening/lessons').status_code == 404
    refusals = [r.getMessage() for r in caplog.records if 'refused' in r.getMessage()]
    assert len(refusals) == 1 and USER_B[:8] in refusals[0] and USER_B not in refusals[0]
    assert a.get('/api/listening/lessons').status_code == 200 and len([r for r in caplog.records if 'refused' in r.getMessage()]) == 1
