"""AUD-120: lesson access reliability on the server. The attempt-start request id lifecycle, support references and safe diagnostics.

Mock tests establish plumbing, not production behaviour: they reproduce the 409 the learner saw and prove the recovery path the
client now takes (re-read the current attempt, never duplicate an attempt, never cross learners).
"""
import logging
import re
from uuid import uuid4

from sqlalchemy import select, func

from app.listening import schema as l, store
from app.listening.domain import LessonContent
from app.listening.seed import load_fixture
from test_listening import accounts, answer, fixture_json, local, make_repository, start, CORRECT      # noqa: F401  (fixtures)


def second_lesson(repo, slug='second-lesson'):
    content, audio, _ = load_fixture(journey=False)
    with repo.engine.begin() as db:
        store.publish_version(db, LessonContent.model_validate(fixture_json() | {'slug': slug}), audio)
        return db.execute(select(l.lessons.c.id).where(l.lessons.c.slug == slug)).scalar_one()


def attempts(repo):
    with repo.engine.connect() as db:
        return db.execute(select(func.count()).select_from(l.attempts)).scalar_one()


def test_the_409_is_reproduced_by_a_request_id_that_already_belongs_to_another_lesson(local, caplog):
    client, repo, first, _, _ = local
    other = second_lesson(repo)
    assert start(client, first, 'request-poisoned').status_code == 200
    caplog.set_level(logging.WARNING)
    refused = start(client, other, 'request-poisoned')
    assert refused.status_code == 409
    assert refused.headers['X-Audli-Error-Code'] == 'request_id_conflict'
    assert re.fullmatch(r'[0-9a-f]{12}', refused.headers['X-Audli-Reference'])
    assert attempts(repo) == 1                                   # the conflicting start created nothing


def test_recovery_after_a_conflict_is_a_fresh_id_and_never_a_duplicate_attempt(local):
    """The client's recovery: re-read the current attempt, else start again with a NEW id. The server resumes an open attempt."""
    client, repo, first, _, _ = local
    other = second_lesson(repo)
    opened = start(client, first, 'request-poisoned').json()
    assert start(client, other, 'request-poisoned').status_code == 409
    assert client.get(f'/api/listening/lessons/{other}/attempts/current').json() == {'attempt_id': None}
    recovered = start(client, other, 'request-fresh-0001')
    assert recovered.status_code == 200 and recovered.json()['attempt']['id'] != opened['attempt']['id']
    again = start(client, other, 'request-fresh-0002')                  # a second tab or a retry: the open attempt is resumed
    assert again.json()['attempt']['id'] == recovered.json()['attempt']['id'] and attempts(repo) == 2


def test_an_interrupted_start_is_replayed_with_the_same_id_without_a_second_attempt(local):
    client, repo, lesson_id, _, _ = local
    first = start(client, lesson_id, 'request-interrupted').json()       # the response was lost; the browser kept the id and sends it again
    replay = start(client, lesson_id, 'request-interrupted').json()
    assert replay['attempt']['id'] == first['attempt']['id'] and attempts(repo) == 1


def test_two_tabs_with_different_ids_share_one_open_attempt(local):
    client, repo, lesson_id, _, _ = local
    tab_a = start(client, lesson_id, 'request-tab-a-0001').json()
    tab_b = start(client, lesson_id, 'request-tab-b-0001').json()
    assert tab_a['attempt']['id'] == tab_b['attempt']['id'] and attempts(repo) == 1


def test_switching_lessons_with_distinct_ids_never_conflicts(local):
    client, repo, first, _, _ = local
    other = second_lesson(repo)
    for n in range(3):
        assert start(client, first, f'request-first-{n:04d}').status_code == 200
        assert start(client, other, f'request-other-{n:04d}').status_code == 200
    assert attempts(repo) == 2


def test_a_request_id_is_never_shared_between_learners(accounts):
    a, b, lesson_id, repo = accounts
    assert start(a, lesson_id, 'request-shared-by-two').status_code == 200
    assert start(b, lesson_id, 'request-shared-by-two').status_code == 200          # the id is unique per learner, not global
    assert attempts(repo) == 2


def test_conflict_log_is_structured_and_carries_no_identifiers_or_content(local, caplog):
    client, repo, first, _, owner = local
    other = second_lesson(repo)
    start(client, first, 'request-secret-id-9999')
    caplog.set_level(logging.WARNING)
    refused = start(client, other, 'request-secret-id-9999')
    text = '\n'.join(r.getMessage() for r in caplog.records)
    reference = refused.headers['X-Audli-Reference']
    assert f'attempt_start_conflict ref={reference} kind=request_id_conflict' in text
    assert f'listening_error ref={reference} method=POST route=/api/listening/lessons/{{lesson_id}}/attempts status=409 kind=request_id_conflict' in text
    for forbidden in ('request-secret-id-9999', owner, first, other, 'Bearer', 'client_request_id', 'sig=', 'exp='):
        assert forbidden not in text


def test_every_listening_response_has_a_reference_and_errors_are_classified(accounts, caplog):
    a, _, lesson_id, _ = accounts
    caplog.set_level(logging.WARNING)
    ok = a.get('/api/listening/lessons')
    assert ok.status_code == 200 and 'X-Audli-Reference' in ok.headers
    missing = a.get(f'/api/listening/attempts/{uuid4()}')
    assert missing.status_code == 404 and 'X-Audli-Reference' in missing.headers
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert f"ref={missing.headers['X-Audli-Reference']} method=GET route=/api/listening/attempts/{{attempt_id}} status=404 kind=not_found" in text
    assert 'listening_error' not in ''.join(r.getMessage() for r in caplog.records if ok.headers['X-Audli-Reference'] in r.getMessage())
