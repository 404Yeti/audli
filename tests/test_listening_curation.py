"""AUD-119: the curated shared library. A private lesson becomes shared only through a recorded review of the exact version, by an operator, in one transaction;
drafts are invisible to every other learner; nothing is copied; no learner route can publish. SQLite always, PostgreSQL when AUDLI_TEST_POSTGRES_URL is set."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, insert, delete, func

from app.listening import library, schema as l, store
from app.listening.api import prepare
from app.listening.objects import LocalObjectStore
from app.listening.seed import seed
from test_listening import make_repository  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lib(make_repository, tmp_path):
    """A full-journey lesson that is private to `curator`, plus another learner who must never see it until it is published."""
    curator, other = str(uuid4()), str(uuid4())
    repo = make_repository(curator)
    make_repository(other)                                                    # a second learner row in the same database
    prepare(repo.engine)
    objects = LocalObjectStore(tmp_path / 'objects', b'k' * 32)
    version_id, _ = seed(repo.engine, objects, journey=True)
    with repo.engine.begin() as db:
        lesson_id = db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one()
        db.execute(insert(l.lesson_owners).values(lesson_id=lesson_id, user_id=curator, created_at=store.utcnow()))       # what generation does: private to the requester
        now = store.utcnow()
        db.execute(insert(l.generation_requests).values(id=str(uuid4()), user_id=curator, client_request_id=uuid4().hex[:16], kind='generate', phase='ready', lesson_id=lesson_id,
                                                        reserved_credits=0, reserved_micro=0, created_at=now, updated_at=now))      # ...and records who generated it
    return type('Lib', (), dict(engine=repo.engine, curator=curator, other=other, lesson=lesson_id, version=version_id, objects=objects, tmp=tmp_path, repo=repo))


def visible_to(lib, user):
    with lib.engine.connect() as db:
        return store.lesson_visible(db, user, lib.lesson), lib.lesson in {c['lesson_id'] for c in store.list_catalog(db, user)}


def entry(lib):
    with lib.engine.connect() as db:
        return dict(db.execute(select(l.library_entries)).mappings().one())


def run(lib, fn, *args, **kwargs):
    if fn in (library.register, library.approve, library.publish, library.unpublish):
        kwargs.setdefault('curators', {lib.curator})                              # the operator's AUDLI_LISTENING_CURATOR_IDS
    with lib.engine.begin() as db:
        return fn(db, *args, **kwargs)


def test_a_private_lesson_is_invisible_to_every_other_learner_and_the_public(lib):
    assert visible_to(lib, lib.curator) == (True, True)
    assert visible_to(lib, lib.other) == (False, False)
    assert visible_to(lib, None) == (False, False)


def test_registering_a_draft_changes_nothing_about_who_can_see_it(lib):
    run(lib, library.register, lib.lesson, 'everyday')
    assert entry(lib)['state'] == 'draft' and entry(lib)['curator_id'] == lib.curator
    assert visible_to(lib, lib.other) == (False, False) and visible_to(lib, lib.curator) == (True, True)


def test_the_full_review_publishes_without_copying_audio_and_unpublishing_restores_privacy(lib):
    with lib.engine.connect() as db:
        audio_before = db.execute(select(l.audio.c.id, l.audio.c.object_key)).all()
        lessons_before, versions_before = (db.execute(select(func.count()).select_from(table)).scalar_one() for table in (l.lessons, l.versions))
    run(lib, library.register, lib.lesson, 'workplace')
    run(lib, library.approve, lib.lesson, 'Robbie', 'listened to all of it')
    assert visible_to(lib, lib.other) == (False, False)                       # approved is still not public
    run(lib, library.publish, lib.lesson)
    assert visible_to(lib, lib.other) == (True, True) and visible_to(lib, None) == (True, True)
    with lib.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.lesson_owners).where(l.lesson_owners.c.lesson_id == lib.lesson)).scalar_one() == 0
        assert db.execute(select(l.audio.c.id, l.audio.c.object_key)).all() == audio_before                       # the same audio rows and object keys: nothing duplicated
        assert (db.execute(select(func.count()).select_from(l.lessons)).scalar_one(), db.execute(select(func.count()).select_from(l.versions)).scalar_one()) == (lessons_before, versions_before)
    published = entry(lib)
    assert published['state'] == 'published' and published['reviewer'] == 'Robbie' and published['published_at'] and published['curator_id'] == lib.curator
    run(lib, library.unpublish, lib.lesson)
    assert visible_to(lib, lib.other) == (False, False) and visible_to(lib, lib.curator) == (True, True) and entry(lib)['state'] == 'unpublished'
    run(lib, library.approve, lib.lesson, 'Robbie')                            # a withdrawn lesson can be reviewed and published again
    run(lib, library.publish, lib.lesson)
    assert visible_to(lib, lib.other) == (True, True)


@pytest.mark.parametrize('order,error', [(('publish',), library.NotFound), (('register', 'publish'), library.Conflict), (('register', 'unpublish'), library.Conflict),
                                         (('approve',), library.NotFound)])
def test_publishing_without_a_recorded_review_is_refused_and_nothing_becomes_public(lib, order, error):
    for step in order[:-1]:
        run(lib, getattr(library, step), lib.lesson, *(['everyday'] if step == 'register' else []))
    with pytest.raises(error):
        run(lib, getattr(library, order[-1]), lib.lesson, *(['Robbie'] if order[-1] == 'approve' else []))
    assert visible_to(lib, lib.other) == (False, False)


def test_an_anonymous_review_or_a_bad_category_is_refused(lib):
    with pytest.raises(library.Invalid):
        run(lib, library.register, lib.lesson, 'gossip')
    run(lib, library.register, lib.lesson, 'travel')
    for reviewer in ('', '   ', 'x' * 81):
        with pytest.raises(library.Invalid):
            run(lib, library.approve, lib.lesson, reviewer)
    with pytest.raises(library.Conflict):
        run(lib, library.register, lib.lesson, 'travel')                       # no double registration
    assert entry(lib)['state'] == 'draft'


def test_an_already_shared_lesson_cannot_be_adopted_so_the_existing_library_is_never_touched(lib):
    with lib.engine.begin() as db:
        db.execute(delete(l.lesson_owners).where(l.lesson_owners.c.lesson_id == lib.lesson))              # shared, like the seeded lessons
    with pytest.raises(library.Conflict):
        run(lib, library.register, lib.lesson, 'everyday')


def test_a_lesson_missing_any_of_the_four_exercises_cannot_be_approved(make_repository, tmp_path):
    curator = str(uuid4())
    repo = make_repository(curator)
    prepare(repo.engine)
    version_id, _ = seed(repo.engine, LocalObjectStore(tmp_path / 'o', b'k' * 32), journey=False)         # the Milestone 01 shape: one question only
    with repo.engine.begin() as db:
        lesson = db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one()
        db.execute(insert(l.lesson_owners).values(lesson_id=lesson, user_id=curator, created_at=store.utcnow()))
        now = store.utcnow()
        db.execute(insert(l.generation_requests).values(id=str(uuid4()), user_id=curator, client_request_id=uuid4().hex[:16], kind='generate', phase='ready', lesson_id=lesson,
                                                        reserved_credits=0, reserved_micro=0, created_at=now, updated_at=now))
        library.register(db, lesson, 'stories', {curator})
    with pytest.raises(library.Invalid, match='exercises are'):
        with repo.engine.begin() as db:
            library.approve(db, lesson, 'Robbie', curators={curator})


def test_approval_does_not_carry_over_to_a_newer_version(lib):
    run(lib, library.register, lib.lesson, 'everyday')
    run(lib, library.approve, lib.lesson, 'Robbie')
    seed(lib.engine, lib.objects, journey=True, revision=2)                    # same lesson, new immutable version
    with pytest.raises(library.Conflict, match='changed after it was approved'):
        run(lib, library.publish, lib.lesson)
    assert visible_to(lib, lib.other) == (False, False)


def test_review_output_has_the_answer_keys_for_the_operator_only(lib):
    run(lib, library.register, lib.lesson, 'everyday')
    sheet = run(lib, library.review_sheet, lib.lesson)
    assert sheet['problems'] == [] and len(sheet['exercises']) == 4 and sheet['transcript']
    assert any(e['answer'] for e in sheet['exercises'] if e['kind'] != 'spoken')


def test_no_learner_route_or_api_code_can_reach_the_library(make_repository):
    source = (ROOT / 'app/listening/api.py').read_text() + (ROOT / 'app/main.py').read_text()
    assert 'app.listening.library' not in source and 'library_entries' not in source           # the API never imports the workflow or reads its table
    from app.config import Settings
    from app.main import create_app
    from conftest import FakeProvider
    paths = set(create_app(Settings(provider='demo', listening_enabled=True, _env_file=None), FakeProvider()).openapi()['paths'])
    assert [p for p in paths if p.startswith('/api/listening')] and not [p for p in paths if 'librar' in p and p.startswith('/api/listening')]


def test_the_operator_command_runs_the_same_workflow_and_refuses_clearly(lib, monkeypatch, capsys):
    monkeypatch.setattr('app.listening.worker.make_engine', lambda settings: lib.engine)
    monkeypatch.setenv('AUDLI_LISTENING_CURATOR_IDS', lib.curator)
    assert library.main(['register', lib.lesson, '--category', 'culture']) == 0
    assert library.main(['publish', lib.lesson]) == 2 and 'Refused' in capsys.readouterr().err                 # not approved yet
    assert library.main(['approve', lib.lesson, '--reviewer', 'Robbie']) == 0
    assert library.main(['publish', lib.lesson]) == 0
    capsys.readouterr()
    assert library.main(['status']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r['state'], r['category']) for r in rows] == [('published', 'culture')]
    assert visible_to(lib, lib.other) == (True, True)


def test_migration_014_is_in_the_ordered_opt_in_and_the_api_does_not_require_its_table():
    from app.listening.migrations import postgres_listening_migration_014
    from scripts.migrate_postgres import LISTENING_MIGRATIONS
    assert (ROOT / 'docs/postgres-014.sql').read_text() == postgres_listening_migration_014()
    assert (14, 'postgres-014.sql') in LISTENING_MIGRATIONS and [v for v, _ in LISTENING_MIGRATIONS] == sorted(v for v, _ in LISTENING_MIGRATIONS)
    assert l.NAMES_014 == ('listening_library_entries',)
    text = postgres_listening_migration_014()
    assert 'ALTER TABLE listening_library_entries ENABLE ROW LEVEL SECURITY' in text and 'REVOKE ALL ON TABLE listening_library_entries' in text
    statements = '\n'.join(line for line in text.splitlines() if not line.startswith('--'))
    assert 'ALTER TABLE listening_lesson' not in statements and 'schema_migrations' not in statements.replace('listening_migrations', '')       # additive; touches no existing table


def test_a_learners_private_lesson_can_never_enter_the_workflow(lib):
    with pytest.raises(library.Conflict, match='not an authorized curator'):
        run(lib, library.register, lib.lesson, 'everyday', curators=set())                                   # nobody is a curator until the operator says so
    with pytest.raises(library.Conflict, match='not an authorized curator'):
        run(lib, library.register, lib.lesson, 'everyday', curators={lib.other})                             # a different account on the list does not help
    assert not entry_rows(lib) and visible_to(lib, lib.other) == (False, False)


def test_an_owned_lesson_that_the_owner_did_not_generate_is_refused(lib):
    with lib.engine.begin() as db:
        db.execute(delete(l.generation_requests))
    with pytest.raises(library.Conflict, match='not generated by its owner'):
        run(lib, library.register, lib.lesson, 'everyday')
    assert not entry_rows(lib)


def test_losing_curator_status_or_ownership_stops_approval_and_publishing(lib):
    run(lib, library.register, lib.lesson, 'everyday')
    with pytest.raises(library.Conflict, match='authorized curator'):
        run(lib, library.approve, lib.lesson, 'Robbie', curators=set())
    run(lib, library.approve, lib.lesson, 'Robbie')
    with pytest.raises(library.Conflict, match='authorized curator'):
        run(lib, library.publish, lib.lesson, curators={lib.other})
    with lib.engine.begin() as db:                                                                              # ownership moved to someone else after approval
        db.execute(l.lesson_owners.update().where(l.lesson_owners.c.lesson_id == lib.lesson).values(user_id=lib.other))
    with pytest.raises(library.Conflict, match='authorized curator'):
        run(lib, library.publish, lib.lesson, curators={lib.curator, lib.other})
    assert visible_to(lib, None) == (False, False)


def test_an_audio_variant_added_after_the_review_invalidates_it(lib):
    run(lib, library.register, lib.lesson, 'everyday')
    run(lib, library.approve, lib.lesson, 'Robbie')
    assert entry(lib)['reviewed_digest']
    with lib.engine.begin() as db:
        first = db.execute(select(l.audio)).mappings().first()
        db.execute(insert(l.audio).values({**dict(first), 'id': str(uuid4()), 'voice': 'unreviewed-voice', 'created_at': store.utcnow()}))
    with pytest.raises(library.Conflict, match='changed after the review'):
        run(lib, library.publish, lib.lesson)
    assert visible_to(lib, lib.other) == (False, False)


def test_a_withdrawn_lesson_goes_back_only_to_a_curator(lib):
    run(lib, library.register, lib.lesson, 'everyday')
    run(lib, library.approve, lib.lesson, 'Robbie')
    run(lib, library.publish, lib.lesson)
    with pytest.raises(library.Invalid, match='authorized curator'):
        run(lib, library.unpublish, lib.lesson, to_user=lib.other)                                               # never hand it to an ordinary learner
    assert visible_to(lib, lib.other) == (True, True)
    run(lib, library.unpublish, lib.lesson)
    assert visible_to(lib, lib.other) == (False, False)


def test_the_library_never_touches_object_storage_and_its_keys_cascade_with_the_lesson():
    source = (ROOT / 'app/listening/library.py').read_text()
    assert 'app.listening.objects' not in source and '.delete(' not in source.replace('db.execute(delete', '')     # no storage call, no object deletion
    cascade = {fk.parent.name: fk.ondelete for fk in l.library_entries.foreign_keys}
    assert cascade['lesson_id'] == 'CASCADE' and cascade['version_id'] == 'CASCADE' and cascade['curator_id'] == 'SET NULL'
    assert any(i.name == 'listening_library_by_curator' for i in l.library_entries.indexes)


def entry_rows(lib):
    with lib.engine.connect() as db:
        return db.execute(select(l.library_entries)).all()
