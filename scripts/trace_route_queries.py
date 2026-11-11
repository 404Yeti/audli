#!/usr/bin/env python3
"""AUD-88: the exact database work of each turn route, block by block, from a real run (no delay needed: counts, not time).

Runs the synthetic lesson over the real app on a real PostgreSQL and prints, per route, every pool checkout in order: whether it was
a read or a write transaction, how many statements it ran and which tables and operations they touched. The cost model matches
production with P2 (autocommit reads) and lazy ping: a read block costs one round trip per statement, a write block costs
BEGIN + statements + COMMIT. Synthetic data only.

    python scripts/trace_route_queries.py --admin-url postgresql://u:p@127.0.0.1:55432/postgres [--route assess] [--memo]
"""
import argparse
import re
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
VERB = re.compile(r'^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b', re.I)
TABLE = re.compile(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)', re.I)


def describe(statement):
    verb = VERB.match(statement)
    verb = verb.group(1).upper() if verb else statement.split()[0].upper()
    tables = []
    for name in TABLE.findall(statement):
        if name.lower() not in tables:
            tables.append(name.lower())
    locking = ' FOR UPDATE' if ' FOR UPDATE' in statement.upper() else ''
    return f"{verb} {','.join(tables[:3])}{locking}"


def trace(admin_url, memo):
    import psycopg
    from psycopg import sql
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    from app.config import Settings
    from app.main import create_app
    from app.repository import PostgresRepository
    from conftest import ASGIClient, FakeProvider
    from lesson_flow import run_lesson
    from scripts.migrate_postgres import migrate
    from analyze_repeated_reads import wav
    name = 'audli_trace_' + uuid.uuid4().hex
    owner = '00000000-0000-4000-8000-0000000000cc'
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    assert migrate(Settings(persistence='postgres', database_url=url, learner_id=owner, _env_file=None)) is True
    repo = PostgresRepository(url, owner)
    state = {'route': None, 'blocks': None, 'block': None}
    requests = []
    try:
        profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
        provider = FakeProvider()
        audio = wav()

        async def speak(text, speech_rate=.9):
            return audio
        provider.speak = speak
        with tempfile.TemporaryDirectory() as data:
            settings = Settings(data_dir=Path(data), db_read_autocommit=True, request_read_memo=memo, _env_file=None) if memo else \
                Settings(data_dir=Path(data), db_read_autocommit=True, _env_file=None)
            client = ASGIClient(create_app(settings, provider, repo), provider)

            @event.listens_for(repo.engine, 'checkout')
            def checkout(dbapi_connection, record, proxy):
                if state['route'] is not None:
                    state['block'] = {'statements': [], 'write': False}
                    state['blocks'].append(state['block'])

            @event.listens_for(repo.engine, 'before_cursor_execute')
            def statement(conn, cursor, text, parameters, context, many):
                block = state['block']
                if state['route'] is not None and block is not None and not text.lstrip().upper().startswith(('SHOW', 'SET')):
                    block['statements'].append(describe(text))
                    if not text.lstrip().upper().startswith(('SELECT', 'WITH')) or ' FOR UPDATE' in text.upper():
                        block['write'] = True
            real = client.request

            def request(method, path, **kw):
                state.update(route=f"{method} {UUID.sub('{id}', path.split('?')[0])}", blocks=[], block=None)
                try:
                    return real(method, path, **kw)
                finally:
                    requests.append((state['route'], [b for b in state['blocks'] if b['statements']]))
                    state['route'] = None
            client.request = request
            run_lesson(client, audio)
    finally:
        repo.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    return requests


def cost(blocks):
    return sum((2 + len(b['statements'])) if b['write'] else len(b['statements']) for b in blocks)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--admin-url', required=True)
    parser.add_argument('--route', default='', help='only routes containing this text')
    parser.add_argument('--which', choices=['first', 'last'], default='first', help='which call of a route to show (the lesson calls assess twice)')
    parser.add_argument('--memo', action='store_true', help='with AUDLI_REQUEST_READ_MEMO on (needs that branch)')
    parser.add_argument('--rtt-ms', type=float, default=72.0)
    args = parser.parse_args()
    requests = trace(args.admin_url, args.memo)
    chosen = {}
    for route, blocks in requests:
        if args.route in route and (args.which == 'last' or route not in chosen):
            chosen[route] = blocks
    for route, blocks in chosen.items():
        total = cost(blocks)
        print(f"\n{route}: {len(blocks)} checkouts, {sum(len(b['statements']) for b in blocks)} statements, {sum(b['write'] for b in blocks)} write transactions = {total} round trips = {total * args.rtt_ms / 1000:.1f} s")
        for index, block in enumerate(blocks, 1):
            kind = 'W' if block['write'] else 'R'
            print(f"   {index:2d} {kind} {len(block['statements']):2d}  " + ' | '.join(block['statements']))


if __name__ == '__main__':
    main()
