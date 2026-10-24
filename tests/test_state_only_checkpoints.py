"""AUD-88: the upload's pending-attempt marker and the assessment checkpoint as one UPDATE of the conversation row
(AUDLI_DB_STATE_ONLY_CHECKPOINTS, default off; extends the AUD-84 P5 technique).

Proof obligations, as for P5: (1) with the flag on every persisted row and every response is identical to the full rewrite,
after EVERY request of a complete lesson, on SQLite and real PostgreSQL; (2) the child rows the full rewrite would touch are
already stored, so only the conversation row (and the attempt's confirmed text) changes; (3) every unusual case falls back to the
unchanged full save and raises its errors; (4) the attempt update and the conversation write stay one transaction; (5) the
statement count really drops; (6) the flag is off by default.
"""
import json
import re
import threading

import pytest
from sqlalchemy import event

from app.config import Settings
from app.main import create_app
from app.models import Conversation
from app.storage import schema as s
from conftest import ASGIClient, FakeProvider
from lesson_flow import run_lesson
from test_state_only_ready import OWNER, OTHER, changed, deterministic_ids, dump, make_repo, normalise  # noqa: F401  (fixtures)

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def lesson_client(repo, tmp_path, audio_bytes, flag, **settings):
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    app = create_app(Settings(data_dir=tmp_path / f'data-{flag}-{len(settings)}', db_state_only_checkpoints=flag, _env_file=None, **settings), provider, repo)
    return ASGIClient(app, provider)


def drive(make_repo, tmp_path, audio_bytes, monkeypatch, flag, **settings):
    """A complete lesson; returns the normalised dump of every table and response after each request."""
    deterministic_ids(monkeypatch)
    repo = make_repo(f'cp-{flag}-{len(settings)}')
    client = lesson_client(repo, tmp_path, audio_bytes, flag, **settings)
    steps, real = [], client.request

    def request(method, path, **kw):
        response = real(method, path, **kw)
        body = response.content if path.endswith('-audio') or '/audio' in path else response.json() if response.content else None
        steps.append((method, UUID.sub('{id}', path), response.status_code, normalise(body) if isinstance(body, (dict, list)) else None, dump(repo)))
        return response
    client.request = request
    ready = run_lesson(client, audio_bytes)
    return repo, steps, normalise(ready)


def test_flag_is_off_by_default():
    assert Settings.model_fields['db_state_only_checkpoints'].default is False


@pytest.mark.parametrize('extra', [{}, {'db_offload': True}])
def test_every_request_of_a_lesson_leaves_identical_rows_and_responses(make_repo, tmp_path, audio_bytes, monkeypatch, extra):
    _, off, off_ready = drive(make_repo, tmp_path, audio_bytes, monkeypatch, False, **extra)
    _, on, on_ready = drive(make_repo, tmp_path, audio_bytes, monkeypatch, True, **extra)
    assert len(on) == len(off) and on_ready == off_ready
    for index, (a, b) in enumerate(zip(off, on)):
        assert a[:4] == b[:4], (index, a[:3])                   # same route, status and response
        assert a[4] == b[4], (index, a[:2], changed(a[4], b[4]))  # same rows in EVERY table after this request


def test_only_the_conversation_row_changes_and_the_full_rewrite_was_a_no_op(make_repo, tmp_path, audio_bytes, monkeypatch):
    deterministic_ids(monkeypatch)
    repo = make_repo('children')
    client = lesson_client(repo, tmp_path, audio_bytes, False)
    run_lesson(client, audio_bytes)
    exercise_id = repo.current_exercise()['id']
    conversation = repo.conversation(exercise_id)
    assert conversation.turns and conversation.followups and conversation.assessment      # real children exist
    attempt_id = conversation.turns[-1].attempt_id
    conversation.pending_attempt_id, conversation.pending_text, conversation.state = attempt_id, 'again', 'ASSESSING'
    before = dump(repo)
    repo.save_conversation(conversation)                                      # the current implementation
    after_full = dump(repo)
    assert changed(before, after_full) == {'conversations'}                     # it rewrote every child, yet only this table changed
    conversation.state = 'AWAITING_SUMMARY'
    repo.save_conversation_checkpoint(conversation)
    after_checkpoint = dump(repo)
    assert changed(after_full, after_checkpoint) == {'conversations'}
    row = json.loads(after_checkpoint['conversations'][0])
    assert row['state'] == 'AWAITING_SUMMARY' and row['pending_attempt_id'] == attempt_id
    # The stored conversation reloads to exactly the object that was saved (transcripts, turns, assessment intact).
    assert normalise(repo.conversation(exercise_id).model_dump(mode='json')) == normalise(conversation.model_dump(mode='json'))


def test_checkpoint_equals_full_save_on_twin_databases(make_repo, tmp_path, audio_bytes, monkeypatch):
    results = []
    for mode in ('full', 'checkpoint'):
        deterministic_ids(monkeypatch)
        repo = make_repo(f'twin-{mode}')
        client = lesson_client(repo, tmp_path / mode, audio_bytes, False)
        run_lesson(client, audio_bytes)
        exercise_id = repo.current_exercise()['id']
        conversation = repo.conversation(exercise_id)
        conversation.pending_attempt_id, conversation.pending_text, conversation.state = conversation.turns[-1].attempt_id, 'x', 'ASSESSING'
        (repo.save_conversation if mode == 'full' else repo.save_conversation_checkpoint)(conversation)
        results.append(dump(repo))
    assert results[0] == results[1]


def test_unusual_cases_fall_back_to_the_full_save_and_its_errors(make_repo, tmp_path, audio_bytes, monkeypatch):
    deterministic_ids(monkeypatch)
    repo = make_repo('fallbacks')
    client = lesson_client(repo, tmp_path, audio_bytes, False)
    run_lesson(client, audio_bytes)
    exercise_id = repo.current_exercise()['id']
    conversation = repo.conversation(exercise_id)
    foreign = repo.for_learner(OTHER)                                           # creating the other learner's own rows is not a write to this one
    before = dump(repo)
    # a pending attempt that does not belong to this exercise: the same error, nothing written
    stranger = conversation.model_copy(deep=True)
    stranger.pending_attempt_id = '00000000-0000-4000-8000-0000000000ff'
    for save in (repo.save_conversation, repo.save_conversation_checkpoint):
        with pytest.raises(ValueError):
            save(stranger)
        assert dump(repo) == before
    # an exercise that is not this learner's: the same error, nothing written
    for save in (foreign.save_conversation, foreign.save_conversation_checkpoint):
        with pytest.raises(ValueError):
            save(conversation)
    assert dump(repo) == before
    # a missing conversation row is re-created by the fallback
    from sqlalchemy import delete
    with repo.transaction() as db:
        for table in (s.evidence, s.assessments, s.turns, s.followups, s.conversations):
            db.execute(delete(table).where(table.c.exercise_id == exercise_id))
    assert repo.conversation(exercise_id) is None
    conversation.pending_attempt_id = None
    repo.save_conversation_checkpoint(conversation)
    assert repo.conversation(exercise_id) is not None


def test_the_attempt_update_and_the_checkpoint_are_one_transaction(make_repo, tmp_path, audio_bytes, monkeypatch):
    deterministic_ids(monkeypatch)
    repo = make_repo('atomic')
    client = lesson_client(repo, tmp_path, audio_bytes, False)
    run_lesson(client, audio_bytes)
    exercise_id = repo.current_exercise()['id']
    conversation = repo.conversation(exercise_id)
    attempt_id = conversation.turns[-1].attempt_id
    conversation.pending_attempt_id, conversation.pending_text, conversation.state = attempt_id, 'confirmed words', 'ASSESSING'
    before = dump(repo)

    def failing(self, db, conversation):
        raise RuntimeError('database went away')
    monkeypatch.setattr(type(repo), 'update_conversation_checkpoint', failing)
    with pytest.raises(RuntimeError):
        repo.prepare_assessment(attempt_id, 'confirmed words', conversation, checkpoint_only=True)
    assert dump(repo) == before                                                 # confirmed_text rolled back with the conversation
    monkeypatch.undo()
    repo.prepare_assessment(attempt_id, 'confirmed words', conversation, checkpoint_only=True)
    assert repo.attempt(attempt_id)['confirmed_text'] == 'confirmed words'
    repo.prepare_assessment(attempt_id, 'confirmed words', conversation, checkpoint_only=True)      # a lost-response retry is idempotent
    assert repo.attempt(attempt_id)['confirmed_text'] == 'confirmed words'
    with pytest.raises(ValueError):                                                                # the checkpoint-mismatch guard is unchanged
        repo.prepare_assessment(attempt_id, 'different words', conversation, checkpoint_only=True)


def test_the_handlers_use_the_new_path_only_when_the_flag_is_on(make_repo, tmp_path, audio_bytes, monkeypatch):
    from app.storage.repository import SQLProgressRepository
    calls = {}
    for flag in (False, True):
        deterministic_ids(monkeypatch)
        calls[flag] = {'save_conversation_checkpoint': 0, 'checkpoint_only': 0}
        original_save, original_prepare = SQLProgressRepository.save_conversation_checkpoint, SQLProgressRepository.prepare_assessment

        def save(self, conversation, _o=original_save, _f=flag):
            calls[_f]['save_conversation_checkpoint'] += 1
            return _o(self, conversation)

        def prepare(self, attempt_id, text, conversation, checkpoint_only=False, _o=original_prepare, _f=flag):
            calls[_f]['checkpoint_only'] += bool(checkpoint_only)
            return _o(self, attempt_id, text, conversation, checkpoint_only)
        monkeypatch.setattr(SQLProgressRepository, 'save_conversation_checkpoint', save)
        monkeypatch.setattr(SQLProgressRepository, 'prepare_assessment', prepare)
        repo = make_repo(f'wiring-{flag}')
        run_lesson(lesson_client(repo, tmp_path, audio_bytes, flag), audio_bytes)
        monkeypatch.undo()
    assert calls[False] == {'save_conversation_checkpoint': 0, 'checkpoint_only': 0}
    assert calls[True]['save_conversation_checkpoint'] >= 1 and calls[True]['checkpoint_only'] >= 1


def test_the_statement_count_drops_on_the_upload_and_the_assessment_checkpoint(make_repo, tmp_path, audio_bytes, monkeypatch):
    counts = {}
    for flag in (False, True):
        deterministic_ids(monkeypatch)
        repo = make_repo(f'count-{flag}')
        client = lesson_client(repo, tmp_path, audio_bytes, flag)
        current, per_route = {}, {}

        @event.listens_for(repo.engine, 'before_cursor_execute')
        def count(conn, cursor, statement, parameters, context, many):
            if current:
                per_route[current['route']] = per_route.get(current['route'], 0) + 1
        real = client.request

        def request(method, path, **kw):
            current['route'] = method + ' ' + UUID.sub('{id}', path)
            try:
                return real(method, path, **kw)
            finally:
                current.clear()
        client.request = request
        run_lesson(client, audio_bytes)
        counts[flag] = per_route
    upload, assess = 'POST /api/exercises/{id}/attempts', 'POST /api/attempts/{id}/assess'
    assert counts[True][upload] < counts[False][upload] and counts[True][assess] < counts[False][assess]
    assert all(counts[True][route] <= counts[False][route] for route in counts[False])


def test_concurrent_checkpoints_and_full_saves_leave_one_valid_state(make_repo, tmp_path, audio_bytes, monkeypatch):
    deterministic_ids(monkeypatch)
    repo = make_repo('threads')
    client = lesson_client(repo, tmp_path, audio_bytes, False)
    run_lesson(client, audio_bytes)
    exercise_id = repo.current_exercise()['id']
    base = repo.conversation(exercise_id)

    def work(index):
        conversation = base.model_copy(deep=True)
        conversation.state = 'AWAITING_SUMMARY'
        (repo.save_conversation_checkpoint if index % 2 else repo.save_conversation)(conversation)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stored = repo.conversation(exercise_id)
    assert stored.state == 'AWAITING_SUMMARY' and len(stored.turns) == len(base.turns) and stored.assessment == base.assessment
