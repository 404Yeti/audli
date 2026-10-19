"""AUD-72 investigation: how many SYNCHRONOUS database statements does each request issue on the event loop?

Every repository call is sync SQLAlchemy inside an async handler, so each statement blocks the loop for one network round trip.
Counts statements per request for one full exercise turn against a disposable local PostgreSQL (zero-delay fake providers).
Production blocking per request = statements x the real app<->database round-trip time (UNKNOWN here; see the table).

    AUDLI_TEST_POSTGRES_URL=postgresql://u:p@127.0.0.1:54329/postgres .venv/bin/python scripts/experiments/db_statements_per_request.py
"""
import asyncio, io, json, math, os, struct, sys, tempfile, wave
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
boot = tempfile.TemporaryDirectory(prefix='audli-stmt-')
os.environ.update({'AUDLI_ENVIRONMENT': 'development', 'AUDLI_PERSISTENCE': 'sqlite', 'AUDLI_DATABASE_URL': '', 'AUDLI_LEARNER_ID': '',
                   'AUDLI_ALLOWED_ORIGINS': '', 'AUDLI_DATA_DIR': boot.name, 'AUDLI_AUTH_MODE': 'local'})
import httpx
from sqlalchemy import event
from sqlalchemy.engine import make_url
from app.config import Settings
from app.main import create_app
from app.storage.adapters import PostgresRepository
from scripts.migrate_postgres import migrate
from conftest import FakeProvider
from test_conversation import assessment

RAW = os.environ['AUDLI_TEST_POSTGRES_URL']


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
    return out.getvalue()


async def main():
    from sqlalchemy import create_engine, text
    admin = create_engine(make_url(RAW).set(drivername='postgresql+psycopg'), isolation_level='AUTOCOMMIT')
    name = 'audli_stmt_' + uuid4().hex
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE {name}'))
    url = make_url(RAW).set(database=name).render_as_string(hide_password=False)
    owner = str(uuid4())
    migrate(Settings(persistence='postgres', database_url=url, learner_id=owner, _env_file=None))
    repo = PostgresRepository(url, owner)
    provider = FakeProvider()
    async def speak(text, speech_rate=.9): return await provider.speech(None)
    provider.speak = speak
    async def assess(content, history): return assessment(content, history)
    provider.assess = assess
    count, spent = [0], [0.0]
    started = {}
    @event.listens_for(repo.engine, 'before_cursor_execute')
    def before(conn, cursor, statement, parameters, context, executemany): started['t'] = perf_counter()
    @event.listens_for(repo.engine, 'after_cursor_execute')
    def after(conn, cursor, statement, parameters, context, executemany):
        count[0] += 1; spent[0] += perf_counter() - started['t']
    app = create_app(Settings(persistence='postgres', database_url=url, learner_id=owner, provider='demo', data_dir=Path(boot.name), _env_file=None), provider, repository=repo)
    rows = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        async def call(label, method, path, **kw):
            count[0], spent[0] = 0, 0.0
            response = await client.request(method, path, **kw)
            rows.append({'request': label, 'status': response.status_code, 'statements': count[0], 'db_ms_local': round(spent[0] * 1000, 1)})
            return response
        await call('PUT /api/profile', 'PUT', '/api/profile', json={'name': 'Maya', 'goal': 'Meetings'})
        await call('GET /api/profile', 'GET', '/api/profile')
        exercise = (await call('POST /api/exercises', 'POST', '/api/exercises')).json()
        await call('POST …/conversation/listened', 'POST', f"/api/exercises/{exercise['id']}/conversation/listened")
        attempt = (await call('POST …/attempts (upload+STT)', 'POST', f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('a.wav', wav(), 'audio/wav')})).json()
        await call('POST …/assess', 'POST', f"/api/attempts/{attempt['id']}/assess", json={'text': attempt['transcription']['text'], 'confirmed': True, 'followup_id': None})
        await call('POST …/coach-audio', 'POST', f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'})
        await call('POST …/conversation/ready', 'POST', f"/api/exercises/{exercise['id']}/conversation/ready")
    repo.close()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
    total = sum(r['statements'] for r in rows[2:])
    table = {str(rtt): round(total * rtt / 1000, 2) for rtt in (1, 3, 20, 80, 200)}
    print(json.dumps({'harness': 'local PostgreSQL 17 (loopback), zero-delay fakes, one exercise turn', 'requests': rows,
                      'statements_per_exercise_turn': total,
                      'event_loop_blocked_seconds_per_turn_at_rtt_ms': table}, indent=1))

asyncio.run(main())
