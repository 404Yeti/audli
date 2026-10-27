#!/usr/bin/env python3
"""AUD-92: synthetic upload latency/rejection benchmark, disposable LOCAL PostgreSQL only.

python scripts/bench_lease_admission.py --admin-url postgresql://...@127.0.0.1:55439/postgres
No production data or external providers. Auth/STT mocked at 300/1100 ms;
local DB latency is NOT production's network latency. Outputs numeric aggregates only.
"""
import argparse
import asyncio
import json
import tempfile
import threading
import uuid
from pathlib import Path
from time import perf_counter
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url

from app.auth import LearnerAuthentication
from app.config import Settings
from app.main import create_app
from app.repository import PostgresRepository
from app.services.demo import DemoProvider
from app.storage.schema import attempts
from bench_app import BenchProvider, supabase_stub, wav
from bench_turns import describe, prepare_database


class Provider(BenchProvider):
    transcriptions = 0
    async def generate(self, profile):
        return await DemoProvider.generate(self, profile)
    async def transcribe(self, audio, filename):
        self.transcriptions += 1
        return await super().transcribe(audio, filename)


def auth_middleware(app):
    app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    while not isinstance(node, LearnerAuthentication):
        node = node.app
    return node


async def measure(url, tokens, enabled, retry, sequential):
    with tempfile.TemporaryDirectory(prefix='audli-admission-bench-') as data:
        provider = Provider()
        repo = PostgresRepository(url, None)
        auth_http = httpx.AsyncClient(transport=httpx.MockTransport(supabase_stub))
        settings = Settings(data_dir=Path(data), provider='demo', auth_mode='supabase',
                            supabase_url='https://bench.invalid', supabase_publishable_key='sb_publishable_bench',
                            persistence='postgres', database_url=url, db_offload=True,
                            db_lazy_ping=True, db_state_only_transitions=True, db_read_autocommit=True,
                            upload_idempotency=True, upload_retry=True, per_learner_locks=True,
                            lease_admission=enabled, _env_file=None)
        app = create_app(settings, provider, repo, auth_client=auth_http)
        auth = auth_middleware(app)
        lock = threading.Lock()
        active = peak = checkouts = 0
        def checkout(*args):
            nonlocal active, peak, checkouts
            with lock:
                active += 1
                checkouts += 1
                peak = max(peak, active)
        def checkin(*args):
            nonlocal active
            with lock:
                active -= 1
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
                exercises = []
                for token in tokens:
                    headers = {'Authorization': 'Bearer ' + token}
                    response = await client.post('/api/exercises', headers=headers)
                    assert response.status_code == 200
                    exercise = response.json()['id']
                    assert (await client.post(f'/api/exercises/{exercise}/conversation/listened', headers=headers)).status_code == 200
                    exercises.append((exercise, headers))
                event.listen(auth.lease_engine, 'checkout', checkout)
                event.listen(auth.lease_engine, 'checkin', checkin)
                audio = wav()
                async def upload(index):
                    exercise, headers = exercises[index % len(exercises)]
                    headers = {**headers, 'X-Audli-Upload-Id': uuid.uuid4().hex}
                    started = perf_counter()
                    latencies = []
                    for send in range(1, 4 if retry else 2):
                        before = perf_counter()
                        response = await client.post(f'/api/exercises/{exercise}/attempts', headers=headers,
                                                     files={'audio': ('synthetic.wav', audio, 'audio/wav')})
                        latencies.append((response.status_code, (perf_counter() - before) * 1000))
                        if response.status_code != 503 or send == 3:
                            break
                        await asyncio.sleep([.3, .9][send - 1])
                    return {'status': response.status_code, 'sends': send, 'ms': (perf_counter() - started) * 1000,
                            'requests': latencies}
                if sequential:
                    rows = [await upload(i) for i in range(6)]
                else:
                    rows = await asyncio.gather(*(upload(i) for i in range(len(tokens))))
                with repo.engine.connect() as db:
                    persisted = db.execute(select(func.count()).select_from(attempts)).scalar_one()
                return {'enabled': enabled, 'retry': retry, 'sequential': sequential,
                        'recordings': len(rows), 'accepted': sum(r['status'] == 200 for r in rows),
                        'failed_retained': sum(r['status'] == 503 for r in rows),
                        'rejected_requests': sum(status == 503 for r in rows for status, _ in r['requests']),
                        'accepted_delivery_ms': describe([r['ms'] for r in rows if r['status'] == 200]),
                        'failed_delivery_ms': describe([r['ms'] for r in rows if r['status'] == 503]),
                        'rejection_ms': describe([ms for r in rows for status, ms in r['requests'] if status == 503]),
                        'actual_sends': sum(r['sends'] for r in rows), 'lease_checkouts': checkouts,
                        'peak_lease_connections': peak, 'stt_calls': provider.transcriptions,
                        'persisted_attempts': persisted, 'permits': auth.admission.snapshot()}
        finally:
            await auth_http.aclose()
            auth.lease_engine.dispose()
            app.state.accent_controls.engine.dispose()
            repo.close()


async def main(args):
    if make_url(args.admin_url).host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('Only a disposable local PostgreSQL server is permitted.')
    rows = []
    for sequential, retry in [(True, False), (False, False), (False, True)]:
        for enabled in (False, True):
            name = 'audli_bench_admission_' + uuid.uuid4().hex
            tokens = [f'bench-admission-{i}' for i in range(1 if sequential else 4)]
            url = prepare_database(args.admin_url, name, tokens)
            try:
                rows.append(await measure(url, tokens, enabled, retry, sequential))
            finally:
                with psycopg.connect(args.admin_url, autocommit=True) as admin:
                    admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    print(json.dumps({'mock_auth_ms': 300, 'mock_stt_ms': 1100, 'database_rtt': 'local; not production', 'cases': rows}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-url', required=True)
    asyncio.run(main(parser.parse_args()))
