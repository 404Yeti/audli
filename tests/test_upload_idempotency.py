"""AUD-89 P1: a recording sent again returns its first attempt (AUDLI_UPLOAD_IDEMPOTENCY, default off).

Proof obligations: (1) off by default, and with the flag off, with no header or with a malformed header nothing changes; (2) a repeat of
the same upload after success, after a lost response, after a failure between the attempt and its pending marker, or concurrently
creates no second attempt, no second transcription and no state change; (3) uniqueness comes from the primary key, so it holds with no
lease and no lock at all, on SQLite and on real PostgreSQL; (4) the account lease and the per-learner lock still serialize the
duplicates; (5) one learner can never reach another's attempt."""
import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Transcription
from app.storage import schema as s
from app.upload_idempotency import NAMESPACE, attempt_id_for, upload_id_from
from conftest import ASGIClient, FakeProvider
from test_auth import USER_A, auth_response, settings_for
from test_auth_lease_exemption import HEADERS, client_for, hold_lease
from test_conversation import scripted
from test_persistence import repository_factory  # noqa: F401  (fixture)
from test_state_only_ready import OWNER, OTHER, dump, make_repo  # noqa: F401  (fixtures and helpers)

UPLOAD = '0123456789abcdef'
ID = {'X-Audli-Upload-Id': UPLOAD}


class CountingProvider(FakeProvider):
    transcriptions = 0

    async def transcribe(self, audio, filename):
        type(self).transcriptions += 1
        return await super().transcribe(audio, filename)


@pytest.fixture(autouse=True)
def reset_count():
    CountingProvider.transcriptions = 0


def build(make_repo, tmp_path, flag=True, label='x', **settings):
    repo = make_repo(label)
    provider = CountingProvider()
    app = create_app(Settings(data_dir=tmp_path / label, upload_idempotency=flag, _env_file=None, **settings), provider, repo)
    return repo, app, ASGIClient(app, provider)


def begin(client):
    exercise = client.post('/api/exercises').json()
    assert client.post(f"/api/exercises/{exercise['id']}/conversation/listened").json()['state'] == 'AWAITING_SUMMARY'
    return exercise['id']


def upload(client, exercise_id, audio, headers=None):
    return client.post(f'/api/exercises/{exercise_id}/attempts', files={'audio': ('response.wav', audio, 'audio/wav')}, headers=headers or {})


def attempts(repo):
    with repo.engine.connect() as db:
        return db.execute(select(func.count()).select_from(s.attempts)).scalar_one()


# ------------------------------------------------------------------------------------------------------- 1. nothing changes
def test_off_by_default_and_reported_by_health(make_repo, tmp_path):
    assert Settings.model_fields['upload_idempotency'].default is False
    _, app, client = build(make_repo, tmp_path, flag=False)
    assert client.get('/api/health').json()['upload_idempotency'] is False
    _, app, client = build(make_repo, tmp_path, flag=True, label='on')
    assert client.get('/api/health').json()['upload_idempotency'] is True


def test_flag_off_ignores_the_header_every_upload_is_a_new_attempt(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path, flag=False)
    exercise = begin(client)
    first, second = upload(client, exercise, audio_bytes, ID), upload(client, exercise, audio_bytes, ID)
    assert first.status_code == second.status_code == 200 and first.json()['id'] != second.json()['id']
    assert uuid.UUID(first.json()['id']).version == 4 and 'x-audli-replayed' not in second.headers
    assert attempts(repo) == 2 and CountingProvider.transcriptions == 2


def test_flag_on_without_a_header_is_the_old_behaviour(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    first, second = upload(client, exercise, audio_bytes), upload(client, exercise, audio_bytes)
    assert first.json()['id'] != second.json()['id'] and uuid.UUID(first.json()['id']).version == 4
    assert attempts(repo) == 2 and CountingProvider.transcriptions == 2


@pytest.mark.parametrize('value', ['', 'ABCDEF0123456789', '0123456789abcde', '0123456789abcdef0', '0123456789abcdeg', 'x' * 300, '0123456789abcdef, 0123456789abcdef'])
def test_malformed_headers_are_treated_as_no_header(make_repo, tmp_path, audio_bytes, value):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    first, second = upload(client, exercise, audio_bytes, {'X-Audli-Upload-Id': value}), upload(client, exercise, audio_bytes, {'X-Audli-Upload-Id': value})
    assert first.status_code == second.status_code == 200 and first.json()['id'] != second.json()['id']
    assert attempts(repo) == 2


def test_the_header_is_read_only_when_present_exactly_once():
    class Headers:
        def __init__(self, *values): self.values = values
        def getlist(self, name): return list(self.values)
    assert upload_id_from(Headers(UPLOAD)) == UPLOAD
    assert upload_id_from(Headers()) is None and upload_id_from(Headers(UPLOAD, UPLOAD)) is None and upload_id_from(Headers('nope')) is None


# ------------------------------------------------------------------------------------------------------- 2. repeats are replays
def test_a_repeat_after_success_returns_the_first_attempt_and_changes_nothing(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    first = upload(client, exercise, audio_bytes, ID)
    assert first.status_code == 200 and 'x-audli-replayed' not in first.headers
    before = dump(repo)
    second = upload(client, exercise, audio_bytes, ID)
    assert second.status_code == 200 and second.headers['x-audli-replayed'] == 'true'
    assert second.json() == first.json() and uuid.UUID(first.json()['id']).version == 5
    assert CountingProvider.transcriptions == 1 and attempts(repo) == 1 and dump(repo) == before


def test_the_attempt_id_is_the_derived_key_and_the_pending_marker_names_it(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    attempt = upload(client, exercise, audio_bytes, ID).json()
    assert attempt['id'] == attempt_id_for(repo.learner_id, exercise, UPLOAD)
    assert repo.conversation(exercise).pending_attempt_id == attempt['id']


def test_a_lost_response_is_recovered_by_sending_the_same_recording_again(make_repo, tmp_path, audio_bytes):
    """The first request ran to completion on the server; the browser never saw the answer."""
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    upload(client, exercise, audio_bytes, ID)                                  # response discarded
    retry = upload(client, exercise, audio_bytes, ID)
    assert retry.status_code == 200 and CountingProvider.transcriptions == 1 and attempts(repo) == 1
    assert repo.conversation(exercise).pending_attempt_id == retry.json()['id']


def test_a_repeat_after_the_assessment_is_still_the_same_attempt_and_the_conversation_is_untouched(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    scripted(client, [{}])
    first = upload(client, exercise, audio_bytes, ID).json()
    assessed = client.post(f"/api/attempts/{first['id']}/assess", json={'text': first['transcription']['text'], 'confirmed': True, 'followup_id': None})
    assert assessed.status_code == 200 and repo.completed(exercise)
    before = dump(repo)
    again = upload(client, exercise, audio_bytes, ID)                          # would be 409 'already complete' without the replay
    assert again.status_code == 200 and again.json()['id'] == first['id'] and again.headers['x-audli-replayed'] == 'true'
    assert dump(repo) == before and CountingProvider.transcriptions == 1
    # The client then asks for the assessment again: the existing lost-response rule returns the saved result, nothing new.
    repeat = client.post(f"/api/attempts/{first['id']}/assess", json={'text': first['transcription']['text'], 'confirmed': True, 'followup_id': None})
    assert repeat.status_code == 200 and dump(repo) == before


def test_a_failure_between_the_attempt_and_its_pending_marker_is_completed_by_the_retry(make_repo, tmp_path, audio_bytes, monkeypatch):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    original = type(repo).save_conversation

    def failing(self, conversation):
        raise RuntimeError('connection lost after the attempt was written')
    monkeypatch.setattr(type(repo), 'save_conversation', failing)
    broken = upload(client, exercise, audio_bytes, ID)
    assert broken.status_code == 503 and attempts(repo) == 1                   # the attempt row was committed, the marker was not
    assert repo.conversation(exercise).pending_attempt_id is None
    monkeypatch.setattr(type(repo), 'save_conversation', original)
    retry = upload(client, exercise, audio_bytes, ID)
    assert retry.status_code == 200 and retry.headers['x-audli-replayed'] == 'true'
    assert CountingProvider.transcriptions == 1 and attempts(repo) == 1        # no second transcription, no second attempt
    assert repo.conversation(exercise).pending_attempt_id == retry.json()['id']
    scripted(client, [{}])
    assert client.post(f"/api/attempts/{retry.json()['id']}/assess", json={'text': retry.json()['transcription']['text'], 'confirmed': True, 'followup_id': None}).status_code == 200


def test_a_replay_never_overwrites_a_newer_recording_of_the_same_exercise(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    first = upload(client, exercise, audio_bytes, ID).json()
    newer = upload(client, exercise, audio_bytes, {'X-Audli-Upload-Id': 'fedcba9876543210'}).json()      # the learner recorded again
    assert repo.conversation(exercise).pending_attempt_id == newer['id']
    replay = upload(client, exercise, audio_bytes, ID)
    assert replay.json()['id'] == first['id'] and repo.conversation(exercise).pending_attempt_id == newer['id']


def test_different_uploads_of_one_exercise_are_different_attempts(make_repo, tmp_path, audio_bytes):
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    ids = {upload(client, exercise, audio_bytes, {'X-Audli-Upload-Id': f'{i:016x}'}).json()['id'] for i in range(4)}
    assert len(ids) == 4 and attempts(repo) == 4 and CountingProvider.transcriptions == 4


# ------------------------------------------------------------------------------------------------------- 3. concurrency
def post_concurrently(app, exercise, audio, count, headers):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*(http.post(f'/api/exercises/{exercise}/attempts', files={'audio': ('response.wav', audio, 'audio/wav')}, headers=headers)
                                          for _ in range(count)))
    return asyncio.run(run())


def test_concurrent_duplicates_create_one_attempt_and_one_transcription(make_repo, tmp_path, audio_bytes):
    repo, app, client = build(make_repo, tmp_path)
    exercise = begin(client)
    responses = post_concurrently(app, exercise, audio_bytes, 6, ID)
    assert [r.status_code for r in responses] == [200] * 6 and len({r.json()['id'] for r in responses}) == 1
    assert sum(1 for r in responses if r.headers.get('x-audli-replayed') == 'true') == 5
    assert attempts(repo) == 1 and CountingProvider.transcriptions == 1
    assert repo.conversation(exercise).pending_attempt_id == responses[0].json()['id']


def test_the_primary_key_alone_prevents_duplicates_with_no_lock_and_no_lease(make_repo, tmp_path, audio_bytes):
    """Many threads, separate connections, no application-level lock at all: exactly one insert wins, all get the same id."""
    repo = make_repo('race')
    client = ASGIClient(create_app(Settings(data_dir=tmp_path / 'race', _env_file=None), CountingProvider(), repo), CountingProvider())
    exercise = client.post('/api/exercises').json()['id']
    key = attempt_id_for(repo.learner_id, exercise, UPLOAD)
    transcription = Transcription(text='I heard that the team rolled back the update.', confidence=.99)
    barrier = threading.Barrier(8)

    def insert(_):
        barrier.wait()
        return repo.add_attempt_once(exercise, transcription, key)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(insert, range(8)))
    assert sorted(created for _, created in results) == [False] * 7 + [True] and {attempt for attempt, _ in results} == {key}
    assert attempts(repo) == 1


def test_another_learner_cannot_reach_or_collide_with_an_attempt(make_repo, tmp_path, audio_bytes):
    repo = make_repo('owner')
    client = ASGIClient(create_app(Settings(data_dir=tmp_path / 'owner', upload_idempotency=True, _env_file=None), CountingProvider(), repo), CountingProvider())
    exercise = client.post('/api/exercises').json()['id']
    mine = upload(client, exercise, audio_bytes, ID).json()['id']
    assert attempt_id_for(OWNER, exercise, UPLOAD) != attempt_id_for(OTHER, exercise, UPLOAD)
    assert attempt_id_for(OWNER, exercise, UPLOAD) != attempt_id_for(OWNER, str(uuid.uuid4()), UPLOAD)
    assert uuid.UUID(mine).version == 5 and NAMESPACE.version == 4
    other = repo.for_learner(OTHER) if hasattr(repo, 'for_learner') else None
    if other is not None:
        with pytest.raises(ValueError):                                        # not their exercise: no insert, no read of the existing row
            other.add_attempt_once(exercise, Transcription(text='x', confidence=.9), mine)
        assert attempts(repo) == 1


# ------------------------------------------------------------------------------------------------------- 4. the account lease
def lease_app(tmp_path, repository, flag=True):
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    provider = CountingProvider()
    app = create_app(settings_for(tmp_path, upload_idempotency=flag), provider, repository=repository, auth_client=http)
    return app, http


def test_duplicates_queued_behind_the_account_lease_create_one_attempt(tmp_path, repository_factory, audio_bytes):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    app, http = lease_app(tmp_path, repo)

    async def run():
        async with client_for(app) as client:
            exercise = (await client.post('/api/exercises', headers=HEADERS)).json()['id']
            blocker, transaction = hold_lease(repo)
            try:
                files = lambda: {'audio': ('response.wav', audio_bytes, 'audio/wav')}
                headers = {**HEADERS, **ID}
                pending = [asyncio.create_task(client.post(f'/api/exercises/{exercise}/attempts', files=files(), headers=headers)) for _ in range(3)]
                await asyncio.sleep(.6)
                waiting = not any(task.done() for task in pending)             # all three are held back by the lease
            finally:
                transaction.rollback(); blocker.close()
            return waiting, [await task for task in pending]
    waiting, responses = asyncio.run(run())
    assert waiting and [r.status_code for r in responses] == [200] * 3 and len({r.json()['id'] for r in responses}) == 1
    assert attempts(repo) == 1 and CountingProvider.transcriptions == 1
    repo.close(); asyncio.run(http.aclose())



# ------------------------------------------------------------------------------------------------------- 5. aborted requests
def aborted_post(app, exercise, audio, headers, delay):
    """Send an upload and abandon it ``delay`` seconds later (the browser gave up); let the server finish whatever it already started."""
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            task = asyncio.create_task(http.post(f'/api/exercises/{exercise}/attempts', files={'audio': ('response.wav', audio, 'audio/wav')}, headers=headers))
            await asyncio.sleep(delay)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(.8)
    asyncio.run(run())


def test_a_request_abandoned_during_transcription_leaves_nothing_and_the_retry_succeeds(make_repo, tmp_path, audio_bytes):
    repo, app, client = build(make_repo, tmp_path)
    exercise = begin(client)
    original = CountingProvider.transcribe

    async def slow(self, audio, filename):
        await asyncio.sleep(.5)
        return await original(self, audio, filename)
    CountingProvider.transcribe = slow
    try:
        aborted_post(app, exercise, audio_bytes, ID, .2)
    finally:
        CountingProvider.transcribe = original
    assert attempts(repo) == 0 and repo.conversation(exercise).pending_attempt_id is None
    retry = upload(client, exercise, audio_bytes, ID)
    assert retry.status_code == 200 and 'x-audli-replayed' not in retry.headers and attempts(repo) == 1


def test_a_request_abandoned_after_the_attempt_was_written_is_replayed_not_repeated(make_repo, tmp_path, audio_bytes, monkeypatch):
    """The ambiguous case: the server finished the work, the browser never saw the response. db_offload keeps the write running
    after the client has gone, exactly as in production."""
    import time
    repo, app, client = build(make_repo, tmp_path, db_offload=True)
    exercise = begin(client)
    original = type(repo).save_conversation

    def slow_marker(self, conversation):
        time.sleep(.4)
        return original(self, conversation)
    monkeypatch.setattr(type(repo), 'save_conversation', slow_marker)
    aborted_post(app, exercise, audio_bytes, ID, .25)
    monkeypatch.setattr(type(repo), 'save_conversation', original)
    assert attempts(repo) == 1 and CountingProvider.transcriptions == 1
    assert repo.conversation(exercise).pending_attempt_id == attempt_id_for(repo.learner_id, exercise, UPLOAD)
    retry = upload(client, exercise, audio_bytes, ID)
    assert retry.status_code == 200 and retry.headers['x-audli-replayed'] == 'true' and retry.json()['id'] == attempt_id_for(repo.learner_id, exercise, UPLOAD)
    assert attempts(repo) == 1 and CountingProvider.transcriptions == 1


# ------------------------------------------------------------------------------------------------------- 6. resumed sessions
def test_a_resumed_session_sees_the_pending_attempt_and_a_resend_from_a_new_server_process_is_a_replay(make_repo, tmp_path, audio_bytes):
    """Nothing about the upload is kept in memory: a restarted server, or the same learner on a new page load, gets the same answer."""
    repo, _, client = build(make_repo, tmp_path)
    exercise = begin(client)
    first = upload(client, exercise, audio_bytes, ID).json()
    fresh = ASGIClient(create_app(Settings(data_dir=tmp_path / 'restarted', upload_idempotency=True, _env_file=None), CountingProvider(), repo), CountingProvider())
    resumed = fresh.get(f'/api/exercises/{exercise}/conversation').json()
    assert resumed['state'] == 'AWAITING_SUMMARY' and resumed['pending_attempt']['id'] == first['id']
    resend = upload(fresh, exercise, audio_bytes, ID)
    assert resend.status_code == 200 and resend.json() == first and resend.headers['x-audli-replayed'] == 'true'
    assert CountingProvider.transcriptions == 1 and attempts(repo) == 1
