"""Offline provider, persistence and authenticated HTTP contract tests."""
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from app.config import Settings
from app.main import create_app
from app.homework import (Capabilities, ListeningContent, SearchPlan, HomeworkEngine,
    DiscoveryUnavailable, search_plan)
from app.services.youtube import YouTubeProvider, parse_video, duration_seconds, youtube_url, valid_url
from app.storage import homework as store, schema as s
from app.storage.repository import utcnow
from app.storage.migrations import postgres_homework_migration
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory
from test_planner import complete, ready

ID = 'abcdefghijk'


def video(identifier=ID, **changes):
    value = {'id':identifier, 'snippet':{'title':'Science of everyday life', 'description':'A short science explanation.',
        'channelTitle':'Example publisher', 'defaultAudioLanguage':'en-US', 'liveBroadcastContent':'none'},
        'contentDetails':{'duration':'PT3M', 'caption':'true'}, 'status':{'privacyStatus':'public', 'uploadStatus':'processed', 'embeddable':False}}
    for section, updates in changes.items():
        value[section].update(updates)
    return value


def plan(**kwargs):
    return SearchPlan(topic='science', queries=['science simple English'], practice_level='B1', **kwargs)


class Provider:
    calls = 0
    fail = False
    def capabilities(self): return Capabilities()
    async def search(self, search):
        self.calls += 1
        if self.fail: raise DiscoveryUnavailable()
        return [ID]
    async def metadata(self, ids, topic): return [parse_video(video(identifier), topic) for identifier in ids]
    def validate(self, content, search): return YouTubeProvider('').validate(content, search)


@pytest.mark.parametrize('value,seconds', [('PT1M',60), ('PT10M',600), ('PT1H2M3S',3723), ('P1DT1S',86401)])
def test_duration(value, seconds): assert duration_seconds(value) == seconds


@pytest.mark.parametrize('identifier', ['short', '../abcdefghijk', 'abcdefghijk&x=1', 'https://example.com', 'abcdefghij!'])
def test_invalid_ids(identifier):
    with pytest.raises(ValueError): youtube_url(identifier)
    assert parse_video(video(identifier), 'science') is None


def test_capability_contract_and_no_caption_authorization():
    content = parse_video(video(), 'science')
    assert valid_url(content) and content.transcript_available
    assert content.transcript_authorization == 'none'
    assert content.estimated_difficulty is None and content.difficulty_confidence is None
    assert content.capabilities == Capabilities()
    assert YouTubeProvider('').capabilities().grounded_evaluation is False
    content.canonical_url = 'https://www.youtube.com.evil/watch?v=' + ID
    assert not valid_url(content)
    engine = HomeworkEngine(Provider())  # Provider has only the four protocol methods.
    assert len(asyncio.run(engine.discover(plan()))) == 1
    assert len(asyncio.run(engine.discover(plan()))) == 1
    assert engine.provider.calls == 1


@pytest.mark.parametrize('changes,eligible', [({},True), ({'status':{'privacyStatus':'private'}},False),
    ({'status':{'uploadStatus':'failed'}},False), ({'snippet':{'liveBroadcastContent':'live'}},False),
    ({'snippet':{'defaultAudioLanguage':'fr'}},False), ({'snippet':{'defaultAudioLanguage':None}},False),
    ({'contentDetails':{'duration':'PT59S'}},False), ({'contentDetails':{'duration':'PT1M'}},True),
    ({'contentDetails':{'duration':'PT10M'}},True), ({'contentDetails':{'duration':'PT10M1S'}},False),
    ({'contentDetails':{'contentRating':{'ytRating':'ytAgeRestricted'}}},False),
    ({'contentDetails':{'regionRestriction':{'allowed':['US']}}},False),
    ({'contentDetails':{'regionRestriction':{'blocked':['US']}}},False),
    ({'snippet':{'title':'Explicit science'}},False), ({'snippet':{'title':'Other', 'description':'travel'}},False)])
def test_eligibility(changes, eligible):
    content = parse_video(video(**changes), 'science')
    assert YouTubeProvider('').validate(content, plan()) is eligible


def test_region_and_malformed_metadata():
    provider = YouTubeProvider('')
    content = parse_video(video(contentDetails={'regionRestriction':{'allowed':['US']}}), 'science')
    assert provider.validate(content, plan(region='US'))
    assert not provider.validate(content, plan(region='GB'))
    for item in ({}, None, {'id':ID}, video(contentDetails={'duration':'nonsense'}), video(snippet={'title':''})):
        assert parse_video(item, 'science') is None


def test_official_search_parsing_failure_and_quota():
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/search'):
            assert request.url.params['safeSearch'] == 'strict'
            assert request.url.params['type'] == 'video'
            return httpx.Response(200,json={'items':[{'id':{'videoId':ID}}, {'id':{'videoId':'bad'}}, {'id':{'videoId':ID}}, None]})
        return httpx.Response(200,json={'items':[video(), video('notvalid')]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = YouTubeProvider('server-secret', client)
            ids = await provider.search(plan()); assert ids == [ID]
            assert len(await provider.metadata(ids, 'science')) == 1
    asyncio.run(run())
    assert all(r.url.host == 'www.googleapis.com' for r in requests)
    assert all('key' not in r.url.params and r.headers['X-Goog-Api-Key'] == 'server-secret' for r in requests)
    for code in (403,429,500,302):
        calls = []
        def failure(request): calls.append(1); return httpx.Response(code,json={'error':{'message':'private provider response'}})
        async def run_failure():
            async with httpx.AsyncClient(transport=httpx.MockTransport(failure)) as client:
                provider=YouTubeProvider('secret',client)
                for _ in range(2):
                    with pytest.raises(DiscoveryUnavailable): await provider.search(plan())
        asyncio.run(run_failure())
        assert len(calls) == (1 if code in (403,429) else 2)


def test_owned_idempotency_leases_duplicates_and_budget(repository_factory):
    repo=repository_factory(); ready(repo); lesson=complete(repo)
    offered=store.offer(repo,lesson.id)
    assert store.offer(repo,lesson.id).id == offered.id
    other=repository_factory(str(uuid4()))
    for action,args in [(store.get,(offered.id,)), (store.reserve,(offered.id,True,'science',10)),
        (store.change_status,(offered.id,'completed')), (store.offer,(lesson.id,))]:
        with pytest.raises(LookupError): action(other,*args)
    first,token=store.reserve(repo,offered.id,True,'science',10)
    with pytest.raises(ValueError,match='being prepared'): store.reserve(repo,offered.id,True,'science',10)
    saved=store.finish(repo,offered.id,token,[parse_video(video(),'science')])
    assert saved.status == 'assigned'
    assert store.reserve(repo,offered.id,True,'other',10)[1] is None
    assert store.finish(repo,offered.id,token,[]).status == 'assigned'
    second=store.offer(repo,complete(repo).id)
    _,token=store.reserve(repo,second.id,True,'science',10)
    assert store.finish(repo,second.id,token,[parse_video(video(),'science')]).status == 'unavailable'
    third=store.offer(repo,complete(repo).id)
    assert store.reserve(repo,third.id,False,'science',0)[0].status == 'skipped'
    fourth=store.offer(repo,complete(repo).id)
    with pytest.raises(ValueError,match='resting today'): store.reserve(repo,fourth.id,True,'science',0)
    assert store.get(repo,fourth.id).status == 'offered'
    assert store.change_status(repo,offered.id,'completed').status == 'completed'
    assert store.change_status(repo,offered.id,'completed').status == 'completed'
    assert store.list_assignments(repo)


@contextmanager
def client_for(tmp_path, enabled=True):
    settings=Settings(provider='demo',data_dir=tmp_path,homework_enabled=enabled,homework_daily_search_limit=10,_env_file=None)
    app=create_app(settings,FakeProvider()); ready(app.state.repository)
    app.state.homework_engine.provider=Provider()
    try:
        yield ASGIClient(app, FakeProvider())
    finally:
        app.state.repository.close()


def test_accept_decline_failure_and_completion_separation(tmp_path):
    with client_for(tmp_path) as client:
        repo=client.app.state.repository
        lesson=complete(repo); state=repo.profile(); roadmap=repo.roadmap()
        offered=client.post(f'/api/lessons/{lesson.id}/homework',json={}).json()
        saved=client.post(f"/api/homework/{offered['id']}/decision",json={'accept':True,'topic':'science'}).json()
        assert saved['status'] == 'assigned' and saved['content']['canonical_url'] == youtube_url(ID)
        assert client.post(f"/api/homework/{offered['id']}/decision",json={'accept':True}).json()['id'] == saved['id']
        assert client.post(f"/api/homework/{offered['id']}/validate",json={}).status_code == 200
        assert client.get('/api/homework').json()['assignments'][0]['id'] == saved['id']
        assert client.post(f"/api/homework/{offered['id']}/status",json={'status':'completed'}).json()['status'] == 'completed'
        assert repo.profile() == state and repo.roadmap() == roadmap
        another=complete(repo)
        offered=client.post(f'/api/lessons/{another.id}/homework',json={}).json()
        assert client.post(f"/api/homework/{offered['id']}/decision",json={'accept':False}).json()['status'] == 'skipped'
        final=complete(repo); client.app.state.homework_engine.provider.fail=True; client.app.state.homework_engine.cache.clear()
        offered=client.post(f'/api/lessons/{final.id}/homework',json={}).json()
        response=client.post(f"/api/homework/{offered['id']}/decision",json={'accept':True})
        assert response.status_code == 200 and response.json()['status'] == 'unavailable'
        assert repo.lesson(final.id).status == 'completed'
        assert len(client.get('/api/plan').json()['upcoming']) == 3
        assert client.post(f"/api/homework/{uuid4()}/decision",json={'accept':True}).status_code == 404
        assert client.post(f"/api/homework/{offered['id']}/decision",json={'accept':True,'user_id':str(uuid4())}).status_code == 422


def test_disabled_and_incomplete_lesson(tmp_path):
    with client_for(tmp_path,False) as client:
        assert client.get('/api/homework').json() == {'enabled':False,'assignments':[]}
        assert client.post(f'/api/lessons/{uuid4()}/homework',json={}).status_code == 404
    with client_for(tmp_path/'enabled') as client:
        lesson=client.post('/api/lessons/start').json()
        assert client.post(f"/api/lessons/{lesson['id']}/homework",json={}).status_code == 409


def test_migration_and_schema_protection():
    assert Path('docs/postgres-005.sql').read_text() == postgres_homework_migration()
    for table in s.HOMEWORK_TABLES:
        assert f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;' in postgres_homework_migration()
    assert 'homework_lesson_owner' in postgres_homework_migration()
    assert 'no_repeated_homework_content' in postgres_homework_migration()
    assert 'homework_reflections' in postgres_homework_migration()


def test_authenticated_homework_isolation(tmp_path, repository_factory):
    from test_auth import settings_for, auth_response, AccountClient, USER_A
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    database=repository_factory(None)
    app=create_app(settings_for(tmp_path,homework_enabled=True,homework_daily_search_limit=10),FakeProvider(),repository=database,auth_client=auth)
    app.state.homework_engine.provider=Provider()
    try:
        a=AccountClient(app,FakeProvider(),'valid-a'); b=AccountClient(app,FakeProvider(),'valid-b')
        scoped=database.for_learner(USER_A); ready(scoped); lesson=complete(scoped)
        endpoint=f'/api/lessons/{lesson.id}/homework'
        assert ASGIClient(app,FakeProvider()).post(endpoint,json={}).status_code == 401
        assert b.post(endpoint,json={}).status_code == 404
        saved=a.post(endpoint,json={}).json(); identifier=saved['id']
        for suffix,body in [('decision',{'accept':True}),('status',{'status':'completed'}),('validate',{})]:
            assert b.post(f'/api/homework/{identifier}/{suffix}',json=body).status_code == 404
        assert b.get('/api/homework').json()['assignments'] == []
        assert a.post(f'/api/homework/{identifier}/decision',json={'accept':True,'topic':'science'}).json()['status'] == 'assigned'
        assert len(a.get('/api/homework').json()['assignments']) == 1
    finally:
        asyncio.run(auth.aclose())


def test_revalidation_removed_network_failure_and_expired_lease(tmp_path):
    with client_for(tmp_path) as client:
        repo=client.app.state.repository; lesson=complete(repo)
        offered=store.offer(repo,lesson.id)
        _,token=store.reserve(repo,offered.id,True,'science',10)
        with repo.transaction() as db:
            db.execute(s.homework.update().where(s.homework.c.id == offered.id).values(lease_until=utcnow()-timedelta(seconds=1)))
        _,replacement=store.reserve(repo,offered.id,True,'science',10)
        assert replacement != token
        assert store.finish(repo,offered.id,token,[parse_video(video(),'science')]).status == 'offered'
        saved=store.finish(repo,offered.id,replacement,[parse_video(video(),'science')])
        content=saved.content; content.validated_at=utcnow()-timedelta(hours=2)
        with repo.transaction() as db:
            db.execute(s.listening_content.update().where(s.listening_content.c.id == content.id).values(data=content.model_dump(mode='json'),validated_at=content.validated_at))
        async def failure(ids, topic): raise DiscoveryUnavailable()
        client.app.state.homework_engine.provider.metadata=failure
        assert client.post(f'/api/homework/{saved.id}/validate',json={}).status_code == 503
        assert store.get(repo,saved.id).status == 'assigned'
        async def removed(ids, topic): return []
        client.app.state.homework_engine.provider.metadata=removed
        assert client.post(f'/api/homework/{saved.id}/validate',json={}).json()['status'] == 'unavailable'
        assert repo.lesson(lesson.id).status == 'completed'


def test_search_plan_uses_owned_context_and_sanitizes_external_terms(repository_factory):
    repo=repository_factory(); ready(repo); lesson=complete(repo)
    profile=repo.profile(); profile.accent_preferences.accents=['british']
    generated=search_plan(profile,lesson)
    assert generated.topic == 'science' and 'british' in generated.queries[0]
    assert 'science' in generated.queries[0]
    override=search_plan(profile,lesson,'space | cats -dogs https://evil.test')
    assert all(char not in override.queries[0] for char in '|:/')
    assert override.practice_level == profile.difficulty.vocabulary_level


def test_real_completion_endpoint_does_not_discover_or_wait(tmp_path):
    from app.lesson import Lesson, now
    with client_for(tmp_path) as client:
        repo=client.app.state.repository
        at=now()
        lesson=Lesson(id=str(uuid4()),name='Maya',focus='details',started_at=at,active_since=at,
            phase='CLOSING',closing='You caught the main idea. Keep listening for the details.',exercises_completed=1)
        repo.create_lesson(lesson)
        provider=client.app.state.homework_engine.provider; provider.fail=True
        response=client.post(f'/api/lessons/{lesson.id}/heard',json={'revision':0})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed'
        assert provider.calls == 0
        assert store.list_assignments(repo) == []
        offered=client.post(f'/api/lessons/{lesson.id}/homework',json={}).json()
        assert client.post(f"/api/homework/{offered['id']}/decision",json={'accept':True}).json()['status'] == 'unavailable'
        assert repo.lesson(lesson.id).status == 'completed'


def test_import_homework_only_for_source_owner(repository_factory,tmp_path):
    from scripts.import_sqlite import import_learner
    from app.repository import ProgressRepository
    source=ProgressRepository(tmp_path/'source-homework.sqlite3')
    ready(source); lesson=complete(source); offered=store.offer(source,lesson.id)
    _,token=store.reserve(source,offered.id,True,'science',10)
    saved=store.finish(source,offered.id,token,[parse_video(video(),'science')])
    target=repository_factory()
    try:
        import_learner(source.path,target)
        assert store.get(target,saved.id).content.provider_id == ID
        assert store.get(target,saved.id).status == 'assigned'
    finally:
        source.close()


def test_homework_postgres_rls_and_cross_owner_foreign_keys(repository_factory):
    from sqlalchemy.exc import IntegrityError
    repo=repository_factory(); ready(repo); lesson=complete(repo)
    other=repository_factory(str(uuid4())); ready(other)
    with pytest.raises(IntegrityError), other.transaction() as db:
        at=utcnow()
        db.execute(s.homework.insert().values(id=str(uuid4()),user_id=other.learner_id,lesson_id=lesson.id,
            status='offered',objective='Listen.',attempts=0,created_at=at,updated_at=at))
    if repo.engine.dialect.name == 'postgresql':
        from sqlalchemy import text
        with repo.engine.connect() as db:
            for table in s.HOMEWORK_TABLES:
                assert db.execute(text('SELECT relrowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)'), {'name':table.name}).scalar_one()
                for role in ('anon','authenticated'):
                    if db.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role':role}).first():
                        assert not db.execute(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"), {'role':role,'table':table.name}).scalar_one()


def test_provider_logs_neither_credential_nor_profile_topic(caplog):
    import logging
    caplog.set_level(logging.INFO,logger='httpx')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'items':[]}))) as client:
            await YouTubeProvider('private-server-credential',client).search(SearchPlan(topic='privateinterest',queries=['privateinterest'],practice_level='B1'))
    asyncio.run(run())
    assert 'private-server-credential' not in caplog.text
    assert 'privateinterest' not in caplog.text


def test_disabled_homework_does_not_require_optional_tables(tmp_path):
    from app.repository import ProgressRepository
    repo=ProgressRepository(tmp_path/'audli.sqlite3'); ready(repo)
    with repo.transaction() as db:
        for table in reversed(s.HOMEWORK_TABLES): table.drop(db)
        db.execute(s.versions.delete().where(s.versions.c.version == 5))
    settings=Settings(provider='demo',data_dir=tmp_path,_env_file=None)
    app=create_app(settings,FakeProvider(),repo)
    try:
        client=ASGIClient(app,FakeProvider())
        assert client.get('/api/homework').json() == {'enabled':False,'assignments':[]}
        assert client.get('/api/plan').status_code == 200
        settings.homework_enabled=True
        with pytest.raises(RuntimeError,match='migration 005'): create_app(settings,FakeProvider(),repo)
    finally:
        repo.close()


def test_concurrent_offer_and_discovery_reservation_are_idempotent(repository_factory):
    from concurrent.futures import ThreadPoolExecutor
    repo=repository_factory(); ready(repo); lesson=complete(repo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        offers=list(pool.map(lambda _:store.offer(repo,lesson.id),range(2)))
    assert offers[0].id == offers[1].id
    def reserve(_):
        try: return store.reserve(repo,offers[0].id,True,'science',10)[1]
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens=list(pool.map(reserve,range(2)))
    assert sum(token is not None for token in tokens) == 1
    assert store.finish(repo,offers[0].id,next(token for token in tokens if token),[parse_video(video(),'science')]).status == 'assigned'


def test_shared_video_metadata_never_contains_another_learners_topic(repository_factory):
    first=repository_factory(); second=repository_factory(str(uuid4()))
    saved=[]
    for repo,private_topic in [(first,'science first private interest'),(second,'science second private interest')]:
        ready(repo); offered=store.offer(repo,complete(repo).id)
        _,token=store.reserve(repo,offered.id,True,private_topic,10)
        saved.append(store.finish(repo,offered.id,token,[parse_video(video(snippet={'categoryId':'28'}),private_topic)]))
    assert saved[0].content.id == saved[1].content.id
    assert store.get(first,saved[0].id).topic == 'science first private interest'
    assert store.get(second,saved[1].id).topic == 'science second private interest'
    for repo,item in [(first,saved[0]),(second,saved[1])]:
        assert store.get(repo,item.id).content.topic == '28'
