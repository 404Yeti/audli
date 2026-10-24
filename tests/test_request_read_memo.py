"""AUD-87: repeat reads inside one request are answered once (AUDLI_REQUEST_READ_MEMO, default off)."""
import asyncio

import pytest
from sqlalchemy import event

from app.auth import RequestRepository
from app.config import Settings
from app.main import create_app
from conftest import ASGIClient, FakeProvider
from lesson_flow import run_lesson
from test_state_only_ready import deterministic_ids, dump, make_repo, normalise  # noqa: F401  (fixtures)


class Fake:
    learner_id = 'learner'

    def __init__(self):
        self.calls, self.row = [], {'value': 1, 'items': [1]}

    def conversation(self, exercise_id):
        self.calls.append(('conversation', exercise_id))
        return {'id': exercise_id, **{k: list(v) if isinstance(v, list) else v for k, v in self.row.items()}}

    def exercise(self, exercise_id):
        self.calls.append(('exercise', exercise_id))
        if exercise_id == 'boom':
            raise RuntimeError('database down')
        return {'id': exercise_id}

    def save_conversation(self, value):
        self.calls.append(('save', None)); self.row['value'] = value

    def transaction(self):
        repository = self

        class Block:
            def __enter__(self_inner):
                repository.calls.append(('enter', None)); return self_inner

            def __exit__(self_inner, *exc):
                repository.row['value'] = 'written in transaction'
        return Block()

    def other_read(self):
        self.calls.append(('other_read', None)); return 'x'


def proxy(memo=True):
    fake = Fake()
    request = RequestRepository(fake)
    if memo:
        request.memo.set({})
    return request, fake


def test_off_by_default():
    assert Settings.model_fields['request_read_memo'].default is False


def test_without_a_memo_every_read_reaches_the_repository():
    request, fake = proxy(memo=False)
    request.conversation('e'); request.conversation('e')
    assert len(fake.calls) == 2


def test_a_repeat_read_is_answered_once_and_callers_cannot_corrupt_it():
    request, fake = proxy()
    first = request.conversation('e')
    first['items'].append(99); first['value'] = 'mutated by a caller'
    second = request.conversation('e')
    assert fake.calls == [('conversation', 'e')]                        # one database read
    assert second == {'id': 'e', 'value': 1, 'items': [1]}               # a pristine copy, not the caller's object
    request.conversation('other'); assert len(fake.calls) == 2           # different arguments are different reads


def test_any_other_call_forgets_everything_and_the_next_read_sees_the_write():
    request, fake = proxy()
    assert request.conversation('e')['value'] == 1
    request.save_conversation('saved')
    assert request.conversation('e')['value'] == 'saved'
    request.other_read()                                                 # even an unknown read invalidates, conservatively
    request.conversation('e')
    assert [c[0] for c in fake.calls] == ['conversation', 'save', 'conversation', 'other_read', 'conversation']


def test_a_transaction_block_invalidates_on_entry_and_on_exit():
    request, fake = proxy()
    request.conversation('e')
    with request.transaction():
        inside = request.conversation('e')
    after = request.conversation('e')
    assert after['value'] == 'written in transaction' and inside['value'] == 1
    assert [c[0] for c in fake.calls].count('conversation') == 3


def test_failures_are_not_remembered_and_unhashable_arguments_just_read():
    request, fake = proxy()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            request.exercise('boom')
    assert fake.calls.count(('exercise', 'boom')) == 2
    request.exercise(['unhashable']); request.exercise(['unhashable'])
    assert fake.calls.count(('exercise', ['unhashable'])) == 2


def test_the_memo_is_per_request():
    request, fake = proxy()
    request.conversation('e')
    request.memo.set({})                                                 # the next request starts empty
    request.conversation('e')
    assert len(fake.calls) == 2


# ----------------------------------------------------------------------------------------------- the real app
def drive(make_repo, tmp_path, audio_bytes, monkeypatch, flag):
    deterministic_ids(monkeypatch)
    repo = make_repo(f'memo-{flag}')
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    client = ASGIClient(create_app(Settings(data_dir=tmp_path / f'd-{flag}', request_read_memo=flag, _env_file=None), provider, repo), provider)
    counts = {}
    real = client.request
    current = {}

    @event.listens_for(repo.engine, 'before_cursor_execute')
    def count(conn, cursor, statement, parameters, context, many):
        if current:
            counts[current['route']] = counts.get(current['route'], 0) + 1

    def request(method, path, **kw):
        import re
        current['route'] = method + ' ' + re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27}', '{id}', path)
        try:
            return real(method, path, **kw)
        finally:
            current.clear()
    client.request = request
    ready = run_lesson(client, audio_bytes)
    return normalise(ready), dump(repo), counts


def test_a_whole_lesson_is_identical_and_needs_fewer_statements(make_repo, tmp_path, audio_bytes, monkeypatch):
    off_ready, off_rows, off_counts = drive(make_repo, tmp_path, audio_bytes, monkeypatch, False)
    on_ready, on_rows, on_counts = drive(make_repo, tmp_path, audio_bytes, monkeypatch, True)
    assert on_ready == off_ready and on_rows == off_rows                  # same responses, same rows in every table
    assert all(on_counts[route] <= off_counts[route] for route in off_counts), (on_counts, off_counts)
    assert sum(on_counts.values()) < sum(off_counts.values())
    assert on_counts['POST /api/attempts/{id}/assess'] < off_counts['POST /api/attempts/{id}/assess']
