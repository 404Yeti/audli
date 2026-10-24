"""AUD-84: before/after cost of every route of a lesson for ANY boolean setting, on a real PostgreSQL (P2: db_read_autocommit).

Runs the same deterministic synthetic lesson with the chosen setting off and on, against fresh databases, through
an optional delay proxy (scripts/pg_delay_proxy.py) that gives the local server a production-like round trip. Per request it
records wall time, statements, pool checkouts and transactions; Postgres round trips are modelled as
checkouts (ping) + 2 x transactions that are not AUTOCOMMIT (BEGIN, COMMIT/ROLLBACK) + statements.

    python scripts/measure_flag_latency.py --flag db_read_autocommit --admin-url postgresql://u:p@127.0.0.1:55432/postgres --via-port 55433 --runs 5
Synthetic data only; no provider calls.
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import tempfile
import uuid
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))

from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repository import PostgresRepository  # noqa: E402
from conftest import ASGIClient, FakeProvider  # noqa: E402
from lesson_flow import run_lesson  # noqa: E402
from test_conversation import scripted  # noqa: E402,F401
import test_persistence  # noqa: E402,F401

OWNER = '00000000-0000-4000-8000-0000000000aa'
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def audio() -> bytes:
    import io, math, struct, wave
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
    return out.getvalue()


def one_run(admin_url, via_port, setting, flag, wav):
    import psycopg
    from psycopg import sql
    from scripts.migrate_postgres import migrate
    name = 'audli_measure_' + uuid.uuid4().hex
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    direct = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    assert migrate(Settings(persistence='postgres', database_url=direct, learner_id=OWNER, _env_file=None)) is True
    url = make_url(direct).set(port=via_port).render_as_string(hide_password=False) if via_port else direct
    repo = PostgresRepository(url, OWNER)
    rows, live = [], {}
    try:
        profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
        provider = FakeProvider()

        async def speak(text, speech_rate=.9):
            return wav
        provider.speak = speak
        with tempfile.TemporaryDirectory() as data:
            client = ASGIClient(create_app(Settings(data_dir=Path(data), **{setting: flag}, _env_file=None), provider, repo), provider)

            @event.listens_for(repo.engine, 'before_cursor_execute')
            def statement(conn, cursor, text, parameters, context, many):
                if live:
                    live['q'] += 1

            @event.listens_for(repo.engine, 'begin')
            def begin(conn):
                if live:
                    live['t'] += 1
                    live['auto'] += conn.get_execution_options().get('isolation_level') == 'AUTOCOMMIT'
            original = repo.engine.pool.connect

            def connect(*a, **k):
                if live:
                    live['c'] += 1
                return original(*a, **k)
            repo.engine.pool.connect = connect
            real = client.request

            def request(method, path, **kw):
                live.update(q=0, c=0, t=0, auto=0)
                started = perf_counter()
                try:
                    return real(method, path, **kw)
                finally:
                    rows.append({'route': f"{method} {UUID.sub('{id}', path.split('?')[0])}", 'ms': (perf_counter() - started) * 1000,
                                 'q': live['q'], 'c': live['c'], 't': live['t'], 'auto': live['auto']})
                    live.clear()
            client.request = request
            run_lesson(client, wav)
    finally:
        repo.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--flag', required=True, help='boolean Settings field to compare, e.g. db_read_autocommit')
    parser.add_argument('--admin-url', required=True)
    parser.add_argument('--via-port', type=int, default=0, help='connect the app through the delay proxy on this port')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    wav = audio()
    result = {}
    for flag in (False, True):
        runs = [one_run(args.admin_url, args.via_port, args.flag, flag, wav) for _ in range(args.runs)]
        by_route = {}
        for rows in runs:
            for row in rows:
                by_route.setdefault(row['route'], []).append(row)
        result['on' if flag else 'off'] = {
            route: {'calls': len(items) / args.runs, 'ms_mean': statistics.fmean(i['ms'] for i in items), 'ms_all': [round(i['ms'], 1) for i in items],
                    'q': items[0]['q'], 'c': items[0]['c'], 't': items[0]['t'], 'auto': items[0]['auto'],
                    'rtt': items[0]['c'] + 2 * (items[0]['t'] - items[0]['auto']) + items[0]['q']}
            for route, items in by_route.items()}
        result['on' if flag else 'off']['_lesson'] = {'ms_mean': statistics.fmean(sum(r['ms'] for r in rows) for rows in runs)}
    print(f"{'route':56s} {'stmts':>6s} {'chk':>4s} {'txn off/on':>10s} {'RTT off':>8s} {'RTT on':>7s} {'ms off':>8s} {'ms on':>8s} {'saved':>7s}")
    total_off = total_on = 0.0
    for route in sorted((r for r in result['off'] if r != '_lesson'), key=lambda r: -result['off'][r]['ms_mean']):
        off, on = result['off'][route], result['on'][route]
        print(f"{route:56s} {off['q']:6d} {off['c']:4d} {off['t']:4d}/{on['t'] - on['auto']:<4d} {off['rtt']:8d} {on['rtt']:7d} {off['ms_mean']:8.0f} {on['ms_mean']:8.0f} {off['ms_mean'] - on['ms_mean']:7.0f}")
    critical = ['POST /api/exercises/{id}/attempts', 'POST /api/attempts/{id}/assess', 'POST /api/exercises/{id}/coach-audio']
    off_c = sum(result['off'][r]['ms_mean'] for r in critical); on_c = sum(result['on'][r]['ms_mean'] for r in critical)
    print(f"\nthree critical-path routes of a turn (attempts + assess + coach-audio): {off_c:.0f} ms -> {on_c:.0f} ms ({off_c - on_c:.0f} ms saved, {100 * (off_c - on_c) / off_c:.0f}%)")
    print(f"whole lesson wall ms (off/on): {result['off']['_lesson']['ms_mean']:.0f} / {result['on']['_lesson']['ms_mean']:.0f}")
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
