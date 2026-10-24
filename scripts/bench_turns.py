#!/usr/bin/env python3
"""AUD-87: repeatable synthetic voice-turn benchmark over the real app (see scripts/bench_app.py).

For each configuration (a set of AUDLI_* environment flags) it creates a fresh PostgreSQL database, starts the real FastAPI app
in its own process behind a delay proxy that gives the database a production-like round trip (default 72 ms), drives virtual
learners over real HTTP exactly as the browser does, and reports the speech-end-to-coaching proxy (upload start to the end of the
coaching audio response), per-route times, and server-side lock, connection and event-loop figures.

    python scripts/bench_turns.py --admin-url postgresql://u:p@127.0.0.1:55432/postgres \
        --config baseline:AUDLI_DB_READ_AUTOCOMMIT=true --config ack:AUDLI_DB_READ_AUTOCOMMIT=true,AUDLI_ACK_LEASE_EXEMPT=true

Turn model (from the browser code and production logs): upload; assessment starts at once; if it has not finished after 1.2 s the
acknowledgment cue is requested; coaching audio is requested when the assessment AND the cue request have returned.
Synthetic data only: no production learner data, no external providers, never run against production.
"""
import argparse
import asyncio
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
SUMMARY = re.compile(r'Latency summary (\{.*\})')


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 1)


def describe(values):
    return {'n': len(values), 'mean': round(statistics.fmean(values), 1) if values else None, 'p50': pct(values, .5),
            'p95': pct(values, .95), 'max': round(max(values), 1) if values else None}


def route_name(method, path):
    return f"{method} {UUID.sub('{id}', path.split('?')[0])}"


class Learner:
    def __init__(self, client, token, rows):
        self.client, self.token, self.rows = client, token, rows
        self.headers = {'Authorization': 'Bearer ' + token}

    async def call(self, method, path, **kw):
        started = perf_counter()
        headers = {**self.headers, **kw.pop('headers', {})}
        response = await self.client.request(method, path, headers=headers, **kw)
        ended = perf_counter()
        self.rows.append({'route': route_name(method, path), 'ms': (ended - started) * 1000, 'status': response.status_code})
        return response, started, ended

    async def exercise(self, wav, ack_after=1.2):
        """One exercise = two turns (summary, then one follow-up). Returns the turn records."""
        response, *_ = await self.call('POST', '/api/exercises')
        exercise = response.json()
        await self.call('POST', f"/api/exercises/{exercise['id']}/conversation/listened")
        turns, followup = [], None
        for index in range(2):
            turn = {}
            t0 = perf_counter()
            upload, started, ended = await self.call('POST', f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('response.wav', wav, 'audio/wav')})
            attempt = upload.json()
            turn['upload_ms'] = (ended - started) * 1000
            assess_started = perf_counter()
            assess = asyncio.create_task(self.call('POST', f"/api/attempts/{attempt['id']}/assess", json={
                'text': attempt['transcription']['text'], 'confirmed': True, 'followup_id': followup['id'] if followup else None}))
            ack = None
            done, _ = await asyncio.wait({assess}, timeout=ack_after)
            if not done:
                ack = asyncio.create_task(self.call('POST', '/api/recognition/acknowledgment-audio', json={'cue': 'assessment'}))
            result_response, _, assess_ended = await assess
            turn['assess_ms'] = (assess_ended - assess_started) * 1000
            ack_ended = None
            if ack is not None:
                _, ack_started, ack_ended = await ack
                turn['ack_ms'] = (ack_ended - ack_started) * 1000
                turn['ack_after_assess_ms'] = (ack_ended - assess_ended) * 1000
            result = result_response.json()
            cue = 'feedback' if result.get('state') != 'AWAITING_FOLLOWUP' else result['active_followup']['id']
            coach_response, coach_started, coach_ended = await self.call('POST', f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': cue},
                                                                        headers={'Accept': 'audio/*'})
            turn['coach_gap_ms'] = (coach_started - assess_ended) * 1000
            turn['coach_ms'] = (coach_ended - coach_started) * 1000
            turn['turn_ms'] = (coach_ended - started) * 1000       # upload start -> end of coaching audio
            turn['ok'] = upload.status_code == 200 and result_response.status_code == 200 and coach_response.status_code == 200
            turns.append(turn)
            followup = result.get('active_followup')
        await self.call('POST', f"/api/exercises/{exercise['id']}/conversation/ready")
        return turns


async def probe(client, stop, samples):
    """Black-box event-loop responsiveness: latency of the unauthenticated health check, ten times a second."""
    while not stop.is_set():
        started = perf_counter()
        try:
            await client.get('/api/health')
            samples.append((perf_counter() - started) * 1000)
        except httpx.HTTPError:
            samples.append(60000.0)
        await asyncio.sleep(.1)


async def scenario(base, tokens, turns_per_learner, wav):
    rows, turns, samples, stop = [], [], [], asyncio.Event()
    limits = httpx.Limits(max_connections=200)
    async with httpx.AsyncClient(base_url=base, timeout=180, limits=limits) as client, httpx.AsyncClient(base_url=base, timeout=60) as health:
        learners = [Learner(client, token, rows) for token in tokens]
        watcher = asyncio.create_task(probe(health, stop, samples))

        async def run(learner):
            done = 0
            while done < turns_per_learner:
                turns.extend(await learner.exercise(wav))
                done += 2
        started = perf_counter()
        await asyncio.gather(*(run(learner) for learner in learners))
        wall = perf_counter() - started
        stop.set(); await watcher
    by_route = {}
    for row in rows:
        by_route.setdefault(row['route'], []).append(row['ms'])
    keys = ('upload_ms', 'assess_ms', 'ack_ms', 'ack_after_assess_ms', 'coach_gap_ms', 'coach_ms', 'turn_ms')
    return {'learners': len(tokens), 'turns': len(turns), 'failed_turns': sum(not t['ok'] for t in turns), 'wall_s': round(wall, 1),
            'errors': sum(row['status'] >= 400 for row in rows),
            'turn': {key: describe([t[key] for t in turns if key in t]) for key in keys},
            'routes': {route: describe(values) for route, values in sorted(by_route.items())},
            'health_probe_ms': describe(samples)}


def server_summary(log: str):
    merged = {'stages': {}, 'gauges': {}, 'routes': {}}
    for line in log.splitlines():
        match = SUMMARY.search(line)
        if match:
            part = json.loads(match.group(1))
            for key in merged:
                merged[key].update(part.get(key, {}))
    return merged


def wait_for(port, seconds=60):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if httpx.get(f'http://127.0.0.1:{port}/api/health', timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(.3)
    raise RuntimeError('benchmark server did not start')


def prepare_database(admin_url, name, tokens):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    from app.config import Settings
    from app.repository import PostgresRepository
    from bench_app import learner_uuid
    from scripts.migrate_postgres import migrate
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    direct = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    assert migrate(Settings(persistence='postgres', database_url=direct, learner_id=uuid.uuid4(), _env_file=None)) is True
    for token in tokens:
        repo = PostgresRepository(direct, learner_uuid(token))
        profile = repo.profile(); profile.name = 'Bench'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
        repo.close()
    return direct


async def run_config(args, index, name, flags):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    from bench_app import wav
    db_name = 'audli_bench_' + uuid.uuid4().hex
    tokens = [f'bench-{name}-{i}' for i in range(max(1, args.learners))]
    direct = await asyncio.to_thread(prepare_database, args.admin_url, db_name, tokens)
    proxy_port, server_port = args.port_base + 2 * index, args.port_base + 2 * index + 1
    target = make_url(args.admin_url).port or 5432
    proxy = subprocess.Popen([sys.executable, str(ROOT / 'scripts' / 'pg_delay_proxy.py'), '--listen', str(proxy_port), '--target', str(target),
                              '--one-way-ms', str(args.proxy_ms / 2)])
    env = {**os.environ, **flags, 'PYTHONPATH': f'{args.app_root}:{ROOT / "scripts"}',
           'AUDLI_BENCH_DATABASE_URL': make_url(direct).set(port=proxy_port).render_as_string(hide_password=False)}
    log = tempfile.NamedTemporaryFile('w+', suffix='.log', delete=False)
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', '--factory', '--app-dir', str(ROOT / 'scripts'), 'bench_app:build',
                               '--host', '127.0.0.1', '--port', str(server_port), '--log-level', 'info'], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=args.app_root)
    result = {'name': name, 'flags': flags}
    try:
        await asyncio.to_thread(wait_for, server_port)
        base = f'http://127.0.0.1:{server_port}'
        audio = wav()
        # Warm-up so first-use costs (connections, imports) are not counted.
        await scenario(base, tokens[:1], 2, audio)
        result['sequential'] = await scenario(base, tokens[:1], args.sequential_turns, audio)
        if args.learners > 1:
            result['concurrent'] = await scenario(base, tokens, args.turns_per_learner, audio)
    finally:
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
        proxy.terminate()
        log.seek(0); text = log.read(); log.close(); os.unlink(log.name)
        with psycopg.connect(args.admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(db_name)))
    result['server'] = server_summary(text)
    result['errors_in_log'] = len(re.findall(r'\b(ERROR|Traceback)\b', text))
    print(f"[{name}] done", flush=True)
    return result


def table(results, scenario_name):
    lines = [f'| config | turns | turn p50 (s) | turn p95 (s) | upload p50 | assess p50 | ack p50 | coach gap p50 | coach p50 | health p95 (ms) | health max (ms) |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in results:
        s = r.get(scenario_name)
        if not s:
            continue
        t, h = s['turn'], s['health_probe_ms']
        cell = lambda k: f"{t[k]['p50'] / 1000:.1f}" if t[k]['p50'] is not None else '-'
        lines.append(f"| {r['name']} | {s['turns']} | {t['turn_ms']['p50'] / 1000:.1f} | {t['turn_ms']['p95'] / 1000:.1f} | {cell('upload_ms')} | {cell('assess_ms')} | "
                     f"{cell('ack_ms')} | {cell('coach_gap_ms')} | {cell('coach_ms')} | {h['p95']} | {h['max']} |")
    return '\n'.join(lines)


def server_table(results):
    lines = ['| config | loop.lag mean / max (ms) | auth.lease mean / max (ms) | lease attempts>1 | db.checkout n | db.query n | lease.checkout n | assess queries / checkouts |',
             '|---|---|---|---|---|---|---|---|']
    for r in results:
        st, g, routes = r['server']['stages'], r['server']['gauges'], r['server']['routes']
        lag, lease = st.get('loop.lag', {}), st.get('auth.lease', {})
        attempts = g.get('auth.lease_attempts', {})
        retried = sum(count for level, count in attempts.items() if int(level) > 1)
        assess = routes.get('POST /api/attempts/{attempt_id}/assess', {})
        lines.append(f"| {r['name']} | {lag.get('mean_ms')} / {lag.get('max_ms')} | {lease.get('mean_ms')} / {lease.get('max_ms')} | {retried} | "
                     f"{st.get('db.checkout', {}).get('count')} | {st.get('db.query', {}).get('count')} | {st.get('lease.checkout', {}).get('count')} | "
                     f"{assess.get('queries_mean')} / {assess.get('checkouts_mean')} |")
    return '\n'.join(lines)


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--admin-url', required=True)
    parser.add_argument('--config', action='append', required=True, help='NAME[:FLAG=value,FLAG=value]')
    parser.add_argument('--sequential-turns', type=int, default=20)
    parser.add_argument('--learners', type=int, default=4)
    parser.add_argument('--turns-per-learner', type=int, default=8)
    parser.add_argument('--proxy-ms', type=float, default=72.0, help='database round trip added by the delay proxy')
    parser.add_argument('--port-base', type=int, default=8700)
    parser.add_argument('--parallel', type=int, default=2, help='configurations run at the same time')
    parser.add_argument('--app-root', type=Path, default=ROOT, help='checkout whose app/ the server runs (to compare branches)')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    configs = []
    for spec in args.config:
        name, _, flags = spec.partition(':')
        configs.append((name, dict(item.split('=', 1) for item in flags.split(',') if item)))
    gate = asyncio.Semaphore(args.parallel)

    async def guarded(index, name, flags):
        async with gate:
            return await run_config(args, index, name, flags)
    results = await asyncio.gather(*(guarded(i, n, f) for i, (n, f) in enumerate(configs)))
    print('\n## Sequential (1 learner)\n' + table(results, 'sequential'))
    print('\n## Concurrent (%d learners)\n' % args.learners + table(results, 'concurrent'))
    print('\n## Server side (whole run)\n' + server_table(results))
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == '__main__':
    asyncio.run(main())
