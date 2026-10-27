#!/usr/bin/env python3
"""AUD-92: local-only real-HTTP JSON acknowledgment benchmark, mocked providers.

Run once per source tree, e.g. --app-root /tmp/before versus /tmp/after.
Creates/drops its own disposable LOCAL PostgreSQL database; never use production.
Use a separate local pg_delay_proxy.py to simulate a DB link via --db-port.
Outputs numeric aggregates only, never audio/transcript/token/owner identifiers.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from time import perf_counter
import uuid


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--admin-url', required=True)
    parser.add_argument('--db-port', type=int)
    parser.add_argument('--samples', type=int, default=12)
    parser.add_argument('--uploads', type=int, default=4)
    return parser.parse_args()


async def benchmark(args):
    sys.path[:0] = [str(args.app_root), str(args.app_root / 'scripts')]
    import httpx
    import psycopg
    from psycopg import sql
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    import uvicorn
    from app.auth import LearnerAuthentication
    from app.config import Settings
    from app.main import create_app
    from app.repository import PostgresRepository
    from app.services.demo import DemoProvider
    from bench_app import BenchProvider, learner_uuid, wav
    from bench_turns import describe, prepare_database
    if make_url(args.admin_url).host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('A disposable loopback PostgreSQL server is required.')
    if not 1 <= args.samples <= 100 or not 1 <= args.uploads <= 30:
        raise SystemExit('Sample counts outside bounded benchmark range.')
    name = 'audli_bench_json_' + uuid.uuid4().hex
    direct = prepare_database(args.admin_url, name, ['bench-json'])
    url = make_url(direct).set(port=args.db_port).render_as_string(hide_password=False) if args.db_port else direct
    auth_ms = 300
    async def identity(request):
        await asyncio.sleep(auth_ms / 1000)
        return httpx.Response(200, json={'id': learner_uuid('bench-json'), 'email_confirmed_at': 'yes', 'is_anonymous': False})
    class Provider(BenchProvider):
        async def generate(self, profile): return await DemoProvider.generate(self, profile)
    repository = PostgresRepository(url, None)
    auth_http = httpx.AsyncClient(transport=httpx.MockTransport(identity))
    app = auth = None
    try:
        with tempfile.TemporaryDirectory(prefix='audli-json-bench-') as directory:
            settings = Settings(data_dir=Path(directory), provider='demo', persistence='postgres', database_url=url,
                auth_mode='supabase', supabase_url='https://bench.invalid', supabase_publishable_key='sb_publishable_bench',
                db_offload=True, db_lazy_ping=True, db_read_autocommit=True, db_state_only_transitions=True,
                per_learner_locks=True, upload_retry=True, upload_idempotency=True,
                lease_admission=True, lease_admission_limit=1, _env_file=None)
            app = create_app(settings, Provider(), repository, auth_client=auth_http)
            app.middleware_stack = app.build_middleware_stack(); auth = app.middleware_stack
            while not isinstance(auth, LearnerAuthentication): auth = auth.app
            totals = {'queries': 0, 'checkouts': 0, 'connections': 0, 'rollbacks': 0}
            lock = threading.Lock(); close_ms = []
            def count(key):
                def add(*unused):
                    with lock: totals[key] += 1
                return add
            for engine in [repository.engine, auth.lease_engine, app.state.accent_controls.engine]:
                for event_name, key in [('before_cursor_execute', 'queries'), ('checkout', 'checkouts'),
                                        ('connect', 'connections'), ('rollback', 'rollbacks')]:
                    event.listen(engine, event_name, count(key))
            original_take = auth._take_lease
            class TimedConnection:
                def __init__(self, connection): self.connection = connection
                def close(self):
                    started = perf_counter()
                    try: self.connection.close()
                    finally:
                        with lock: close_ms.append((perf_counter() - started) * 1000)
            def take(key, permit=None):
                connection, acquired = original_take(key, permit)
                return TimedConnection(connection), acquired
            auth._take_lease = take
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_config=None, log_level=None))
            server_task = asyncio.create_task(server.serve(sockets=[sock]))
            async def settle():
                deadline = perf_counter() + 10
                while auth.admission.in_use and perf_counter() < deadline: await asyncio.sleep(.002)
                assert auth.admission.in_use == 0
            try:
                while not server.started: await asyncio.sleep(.001)
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=60) as client:
                    headers = {'Authorization': 'Bearer bench-json'}
                    exercise = (await client.post('/api/exercises', headers=headers)).json()['id']; await settle()
                    path = f'/api/exercises/{exercise}/conversation/listened'
                    assert (await client.post(path, headers=headers)).status_code == 200; await settle()
                    result = {}
                    for delay in [300, 20]:
                        auth_ms = delay; await settle()
                        before = dict(totals); rows = []
                        for _ in range(args.samples):
                            started = perf_counter()
                            async with client.stream('POST', path, headers=headers) as response:
                                ttfb = (perf_counter() - started) * 1000
                                await response.aread()
                                rows.append((response.status_code, ttfb, (perf_counter() - started) * 1000))
                        await settle()
                        result[f'fast_follow_auth_{delay}ms'] = {'requests': len(rows),
                            'accepted': sum(status == 200 for status, _, _ in rows),
                            'response_headers_ms': describe([ttfb for status, ttfb, _ in rows if status == 200]),
                            'body_complete_ms': describe([ms for status, _, ms in rows if status == 200]),
                            'database_operations': {key: totals[key] - before[key] for key in totals}}
                    auth_ms = 300; await settle(); before = dict(totals); rows = []
                    audio = wav()
                    for _ in range(args.uploads):
                        started = perf_counter()
                        async with client.stream('POST', f'/api/exercises/{exercise}/attempts',
                            headers={**headers, 'X-Audli-Upload-Id': uuid.uuid4().hex},
                            files={'audio': ('synthetic.wav', audio, 'audio/wav')}) as response:
                            ttfb = (perf_counter() - started) * 1000; await response.aread()
                            assert response.status_code == 200
                            rows.append((ttfb, (perf_counter() - started) * 1000))
                    await settle()
                    result['uploads'] = {'accepted': len(rows), 'response_headers_ms': describe([a for a, _ in rows]),
                        'body_complete_ms': describe([b for _, b in rows]),
                        'database_operations': {key: totals[key] - before[key] for key in totals}}
                    result['cleanup_ms'] = describe(close_ms)
                    result['final_admission'] = auth.admission.snapshot()
                    return result
            finally:
                server.should_exit = True; await server_task; sock.close()
    finally:
        await auth_http.aclose()
        if auth is not None: auth.lease_engine.dispose()
        if app is not None: app.state.accent_controls.engine.dispose()
        repository.close()
        with psycopg.connect(args.admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    print(json.dumps(asyncio.run(benchmark(options())), indent=2))
