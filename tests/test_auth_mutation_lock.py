"""PostgreSQL request lease across awaits; no provider calls or learner reset."""
import asyncio
from uuid import uuid4
import httpx
import pytest
from app.main import create_app
from conftest import FakeProvider
from test_auth import settings_for
from test_persistence import repository_factory


def test_postgres_mutation_lease_serializes_owners_and_releases_on_cancel(repository_factory, tmp_path):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres cross-worker transaction lease')
    owner = str(uuid4()); other = str(uuid4())
    settings = settings_for(tmp_path, persistence='postgres', database_url=repo.engine.url.render_as_string(hide_password=False))
    def verified(request):
        selected = owner if request.headers['authorization'] == 'Bearer owner' else other
        return httpx.Response(200, json={'id': selected, 'email_confirmed_at': 'yes'})
    async def run():
        auth = httpx.AsyncClient(transport=httpx.MockTransport(verified))
        app = create_app(settings, FakeProvider(), repository=repo, auth_client=auth)
        saving = asyncio.Event(); finish = asyncio.Event()
        @app.post('/api/test-mutation')
        async def mutate():
            return {'saved': True}
        @app.post('/api/test-hold-save')
        async def hold():
            saving.set(); await finish.wait()
            return {'saved': True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            held = asyncio.create_task(client.post('/api/test-hold-save', headers={'Authorization': 'Bearer owner'}))
            await saving.wait()
            independent = await asyncio.wait_for(client.post('/api/test-mutation',
                headers={'Authorization': 'Bearer other'}), timeout=2)
            assert independent.status_code == 200
            waiting = asyncio.create_task(client.post('/api/test-mutation', headers={'Authorization': 'Bearer owner'}))
            await asyncio.sleep(.15)
            assert not waiting.done()
            held.cancel()
            with pytest.raises(asyncio.CancelledError):
                await held
            assert (await asyncio.wait_for(waiting, timeout=2)).status_code == 200
            released = await asyncio.wait_for(client.post('/api/test-mutation',
                headers={'Authorization': 'Bearer owner'}), timeout=2)
            assert released.status_code == 200
        await auth.aclose()
    asyncio.run(run())

