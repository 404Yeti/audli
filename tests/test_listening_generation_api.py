"""The AI lesson generator through the real HTTP API with two real accounts: natural-language and structured requests share one pipeline, every
read is owner-only, limits and the kill switch show the unavailable state, and nothing is queued when a request is refused."""
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.listening import generation as g, generation_jobs as gj, schema as t
from app.listening.generation import FakeInterpreter, FakeScriptWriter, Ledger
from app.listening.generator import run_once
from app.listening.objects import LocalObjectStore
from app.listening.speech import FakeSpeech
from app.main import create_app
from conftest import FakeProvider
from test_auth import AccountClient, auth_response, settings_for
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation import VOICES
from test_listening_library import prepare_all


@pytest.fixture
def api(make_repository, tmp_path):
    repo = make_repository(None)
    prepare_all(repo, tmp_path)
    settings = settings_for(tmp_path, listening_enabled=True, listening_generation_enabled=True)
    app = create_app(settings, FakeProvider(), repository=repo, auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    a, b = AccountClient(app, None, 'valid-a'), AccountClient(app, None, 'valid-b')

    def work(interpreter=None, writer=None, halted=None, speech=None):
        deps = g.Deps(engine=repo.engine, settings=settings, ledger=Ledger(repo.engine, settings), objects=LocalObjectStore(tmp_path / 'listening_objects', b'k' * 32),
                      speech=gj.MeteredSpeech(speech or FakeSpeech(), 0.5), writer=writer or FakeScriptWriter(), interpreter=interpreter or FakeInterpreter(),
                      voices=VOICES, halted=halted)
        done = []
        while (outcome := run_once(deps, 'w')) is not None:
            done.append(outcome)
        return done
    return SimpleNamespace(a=a, b=b, repo=repo, engine=repo.engine, work=work, tmp=tmp_path, settings=settings)


SPEC = {'topic': 'ordering coffee in New York', 'cefr': 'B1', 'accent': 'american', 'minutes': 2, 'format': 'dialogue', 'scenario': ''}


def create(client, spec=SPEC, request=None):
    return client.post('/api/listening/generation/requests', json={'client_request_id': request or uuid4().hex, 'spec': spec, 'text': None})


def test_the_natural_language_journey_interpret_review_generate_play_and_it_is_only_theirs(api):
    sent = api.a.post('/api/listening/generation/interpret', json={'client_request_id': uuid4().hex,
        'text': 'Create a B1 listening lesson about ordering coffee in New York, with American accents, around three minutes long.'})
    assert sent.status_code == 200 and sent.json()['phase'] == 'queued'
    api.work()
    interpreted = api.a.get(f"/api/listening/generation/requests/{sent.json()['id']}").json()
    assert interpreted['phase'] == 'interpreted' and interpreted['spec']['cefr'] == 'B1' and interpreted['spec']['minutes'] == 2 and 'minutes_limited:2' in interpreted['notes']
    made = create(api.a, interpreted['spec'])                                   # the learner reviewed the settings and confirms
    assert made.status_code == 200 and made.json()['phase'] == 'queued'
    for n in range(3):
        status = api.a.get(f"/api/listening/generation/requests/{made.json()['id']}").json()
        assert status['phase'] in ('queued', 'ready')
        api.work()
    final = api.a.get(f"/api/listening/generation/requests/{made.json()['id']}").json()
    assert final['phase'] == 'ready' and final['lesson_id'] and final['failure'] is None
    mine = {i['lesson_id']: i for i in api.a.get('/api/listening/lessons').json()}
    assert final['lesson_id'] in mine and mine[final['lesson_id']]['accents'] == ['american']
    started = api.a.post(f"/api/listening/lessons/{final['lesson_id']}/attempts", json={'client_request_id': uuid4().hex})
    assert started.status_code == 200 and started.json()['audio']['duration_ms'] > 10_000
    # the other learner: not in their library, cannot open it, cannot even see the request
    assert final['lesson_id'] not in [i['lesson_id'] for i in api.b.get('/api/listening/lessons').json()]
    assert api.b.post(f"/api/listening/lessons/{final['lesson_id']}/attempts", json={'client_request_id': uuid4().hex}).status_code == 404
    assert api.b.get(f"/api/listening/generation/requests/{made.json()['id']}").status_code == 404
    assert api.b.get(f"/api/listening/generation/requests/{sent.json()['id']}").status_code == 404
    assert api.b.get(f"/api/listening/lessons/{final['lesson_id']}/attempts/current").json() == {'attempt_id': None}


def test_structured_controls_use_the_same_pipeline_and_are_idempotent(api):
    request = uuid4().hex
    first, again = create(api.a, request=request), create(api.a, request=request)
    assert first.json()['id'] == again.json()['id']
    api.work()
    assert api.a.get(f"/api/listening/generation/requests/{first.json()['id']}").json()['phase'] == 'ready'
    with api.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.generation_requests).where(t.generation_requests.c.kind == 'generate')).scalar_one() == 1


def test_the_settings_are_validated_on_the_server_and_longer_lessons_are_limited_and_said_so(api):
    assert create(api.a, {**SPEC, 'accent': 'irish'}).status_code == 422                 # an unavailable accent cannot be chosen
    assert create(api.a, {**SPEC, 'cefr': 'D9'}).status_code == 422
    assert create(api.a, {**SPEC, 'topic': 'x'}).status_code == 422
    assert api.a.post('/api/listening/generation/requests', json={'client_request_id': uuid4().hex, 'spec': SPEC, 'extra': 1}).status_code == 422
    long = create(api.a, {**SPEC, 'minutes': 5}).json()
    assert long['spec']['minutes'] == 2 and 'minutes_limited:2' in long['notes']


def test_availability_and_the_unavailable_state_after_two_lessons(api):
    assert api.a.get('/api/listening/generation/availability').json() == {'available': True, 'reason': None, 'max_minutes': 2,
        'accents': ['american', 'british', 'australian'], 'per_learner_daily': 2}
    create(api.a); create(api.a)
    third = create(api.a).json()
    assert third['phase'] == 'unavailable' and third['unavailable_reason'] == 'daily_limit'
    assert api.a.get('/api/listening/generation/availability').json()['reason'] == 'daily_limit'
    assert api.b.get('/api/listening/generation/availability').json()['available'] is True
    assert api.work() == ['done', 'done']                                                  # only the two accepted requests ever became jobs


def test_the_kill_switch_refuses_new_requests_and_reports_paused(api):
    from app.listening.ai_budget import AiGuard
    AiGuard(api.engine, api.settings).set_halted(True)
    assert api.a.get('/api/listening/generation/availability').json()['reason'] == 'paused'
    assert create(api.a).status_code == 503
    assert api.a.post('/api/listening/generation/interpret', json={'client_request_id': uuid4().hex, 'text': 'about tea'}).status_code == 503


def test_routes_exist_only_when_generation_is_enabled_and_need_a_signed_in_learner(make_repository, tmp_path):
    repo = make_repository(None)
    prepare_all(repo, tmp_path)
    app = create_app(settings_for(tmp_path, listening_enabled=True), FakeProvider(), repository=repo, auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    assert AccountClient(app, None, 'valid-a').get('/api/listening/generation/availability').status_code == 404
    from conftest import ASGIClient
    enabled = create_app(settings_for(tmp_path, listening_enabled=True, listening_generation_enabled=True), FakeProvider(), repository=repo,
                         auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    assert ASGIClient(enabled, None).get('/api/listening/generation/availability').status_code == 401


def test_responses_never_carry_reservations_provider_text_or_storage_keys(api):
    made = create(api.a).json()
    api.work()
    body = api.a.get(f"/api/listening/generation/requests/{made['id']}").text.lower()
    for word in ('reserved', 'credits', 'audio_key', 'audio_meta', 'object_key', 'sha256', 'api_key', 'elevenlabs', 'openai', 'client_request_id', 'user_id'):
        assert word not in body, word


def test_generation_can_be_limited_to_named_learners_and_everyone_else_sees_nothing(make_repository, tmp_path):
    from test_auth import USER_A
    repo = make_repository(None)
    prepare_all(repo, tmp_path)
    settings = settings_for(tmp_path, listening_enabled=True, listening_generation_enabled=True, listening_gen_allowed_learners=USER_A)
    app = create_app(settings, FakeProvider(), repository=repo, auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    a, b = AccountClient(app, None, 'valid-a'), AccountClient(app, None, 'valid-b')
    assert a.get('/api/listening/generation/availability').status_code == 200 and create(a).status_code == 200
    for call in (lambda: b.get('/api/listening/generation/availability'), lambda: create(b),
                 lambda: b.post('/api/listening/generation/interpret', json={'client_request_id': uuid4().hex, 'text': 'about tea'})):
        response = call()
        assert response.status_code == 404 and response.json() == {'detail': 'Not found.'}
    assert b.get('/api/listening/lessons').status_code == 200                                    # the rest of 2.0 is unaffected
    with repo.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.generation_requests)).scalar_one() == 1
