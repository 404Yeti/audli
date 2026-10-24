#!/usr/bin/env python3
"""AUD-87: which database reads repeat, and how much could request-local caching or a shared cache (Redis) save?

Runs one synthetic lesson over the real HTTP app on a real PostgreSQL, records every statement with its parameters per request
and per pool checkout, then replays the trace against two idealised caches with perfect invalidation (a table's version bumps on
every write to it):

  request-local: a read block is a hit if the same statements with the same parameters already ran EARLIER IN THE SAME REQUEST
                 with no write to the tables involved since;
  shared (Redis): the same, but across requests as well (best case: unlimited TTL, no staleness, no cache round trip).

A "read block" is one pool checkout whose statements are all SELECTs. Skipping a block saves its pool ping and its statements
(1 + statements round trips with the P2 autocommit setting; BEGIN and the closing ROLLBACK are already gone there).

    python scripts/analyze_repeated_reads.py --admin-url postgresql://u:p@127.0.0.1:55432/postgres [--json]
Synthetic data only.
"""
import argparse
import hashlib
import io
import json
import math
import re
import struct
import sys
import tempfile
import uuid
import wave
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
TABLE = re.compile(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)', re.I)


def tables_of(statement):
    return frozenset(name.lower() for name in TABLE.findall(statement))


def digest(statement, parameters):
    return hashlib.sha1((statement + '|' + json.dumps(parameters, default=str, sort_keys=True)).encode()).hexdigest()


def simulate(requests, scope):
    """requests: [(route, [block])]; block = [(digest, tables, is_read)]. Per-route totals for one cache scope."""
    versions = defaultdict(int)
    seen_global = set()
    totals = defaultdict(lambda: {'blocks': 0, 'read_blocks': 0, 'hit_blocks': 0, 'statements': 0, 'saved_statements': 0})
    for route, blocks in requests:
        seen = set() if scope == 'request' else seen_global
        row = totals[route]
        for block in blocks:
            row['blocks'] += 1
            row['statements'] += len(block)
            if all(entry[2] for entry in block):
                row['read_blocks'] += 1
                key = tuple((d, tuple(sorted((t, versions[t]) for t in tables))) for d, tables, _, _ in block)
                if key in seen:
                    row['hit_blocks'] += 1
                    row['saved_statements'] += len(block)
                seen.add(key)
            else:
                for _, tables, is_read, _ in block:
                    if not is_read:
                        for table in tables:
                            versions[table] += 1
    return totals


def repeated_in_request(requests, top=15):
    """The statements most often repeated (same parameters) inside one request, summed over the lesson."""
    counts = defaultdict(int)
    for route, blocks in requests:
        first = {}
        for block in blocks:
            for digest_, _, is_read, snippet in block:
                if is_read:
                    first[digest_] = first.get(digest_, 0) + 1
                    if first[digest_] > 1:
                        counts[snippet] += 1
    return sorted(counts.items(), key=lambda item: -item[1])[:top]


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
        handle.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
    return out.getvalue()


def trace_lesson(admin_url):
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
    name = 'audli_reads_' + uuid.uuid4().hex
    owner = '00000000-0000-4000-8000-0000000000bb'
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
            client = ASGIClient(create_app(Settings(data_dir=Path(data), db_read_autocommit=True, _env_file=None), provider, repo), provider)

            @event.listens_for(repo.engine, 'checkout')
            def checkout(dbapi_connection, record, proxy):
                if state['route'] is not None:
                    state['block'] = []
                    state['blocks'].append(state['block'])

            @event.listens_for(repo.engine, 'before_cursor_execute')
            def statement(conn, cursor, text, parameters, context, many):
                if state['route'] is not None and state['block'] is not None and not text.lstrip().upper().startswith(('SHOW', 'SET')):
                    state['block'].append((digest(text, parameters), tables_of(text), text.lstrip().upper().startswith(('SELECT', 'WITH')),
                                           ' '.join(text.split())[:90]))
            real = client.request

            def request(method, path, **kw):
                state.update(route=f"{method} {UUID.sub('{id}', path.split('?')[0])}", blocks=[], block=None)
                try:
                    return real(method, path, **kw)
                finally:
                    requests.append((state['route'], [b for b in state['blocks'] if b]))
                    state['route'] = None
            client.request = request
            run_lesson(client, audio)
    finally:
        repo.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    return requests


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--admin-url', required=True)
    parser.add_argument('--rtt-ms', type=float, default=72.0)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    requests = trace_lesson(args.admin_url)
    result = {}
    for scope in ('request', 'shared'):
        totals = simulate(requests, scope)
        blocks = sum(t['blocks'] for t in totals.values())
        statements = sum(t['statements'] for t in totals.values())
        saved = sum(t['hit_blocks'] + t['saved_statements'] for t in totals.values())
        total = blocks + statements
        result[scope] = {'requests': len(requests), 'checkout_blocks': blocks, 'statements': statements,
                         'hit_blocks': sum(t['hit_blocks'] for t in totals.values()), 'saved_round_trips': saved, 'round_trips': total,
                         'saved_fraction': round(saved / total, 3), 'saved_ms_per_lesson': round(saved * args.rtt_ms),
                         'by_route': dict(sorted(totals.items(), key=lambda item: -(item[1]['hit_blocks'] + item[1]['saved_statements']))[:12])}
    result['repeated'] = repeated_in_request(requests)
    if args.json:
        print(json.dumps(result, indent=1))
        return
    for scope, label in (('request', 'request-local cache'), ('shared', 'shared cache (Redis best case)')):
        r = result[scope]
        print(f"\n{label}: {r['hit_blocks']} of {r['checkout_blocks']} checkouts, {r['saved_round_trips']} of {r['round_trips']} round trips "
              f"({100 * r['saved_fraction']:.1f}%) avoidable = {r['saved_ms_per_lesson'] / 1000:.1f} s per lesson at {args.rtt_ms:.0f} ms")
        print(f"  {'route':56s} {'blocks':>6s} {'reads':>5s} {'hits':>5s} {'saved stmts':>11s}")
        for route, t in r['by_route'].items():
            print(f"  {route:56s} {t['blocks']:6d} {t['read_blocks']:5d} {t['hit_blocks']:5d} {t['saved_statements']:11d}")
    print('\nmost repeated reads inside one request (count over the lesson):')
    for snippet, count in result['repeated']:
        print(f"  {count:3d}  {snippet}")


if __name__ == '__main__':
    main()
