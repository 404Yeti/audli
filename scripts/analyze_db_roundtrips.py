"""AUD-82: count database work per lesson endpoint by driving a full synthetic lesson through the real HTTP app.

Measurement only. SQLite stands in for Postgres, so the statement and checkout COUNTS come from the real application code
paths (repositories, handlers) while the per-operation latency does not; production's 72 ms round trip is applied separately in
docs/AUD-82-DB-ROUNDTRIP-DESIGN.md. Synthetic data, FakeProvider, no network, no provider calls.

    .venv/bin/python scripts/analyze_db_roundtrips.py [--out report.json] [--show-sql ROUTE_SUBSTRING]

Per request it records: statements (Q), pool checkouts (C), SQLAlchemy transactions begun (T), identical repeated statements
(duplicates) and write-vs-read statements. It also estimates network round trips on Postgres with psycopg 3:
    pool_pre_ping ........ 1 per checkout
    BEGIN ................ 1 per transaction (psycopg sends it as its own command)
    each statement ....... 1
    COMMIT / ROLLBACK .... 1 per transaction (also the reset-on-return rollback after a read)
"""
import argparse
import asyncio
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import io  # noqa: E402
import math  # noqa: E402
import struct  # noqa: E402
import wave  # noqa: E402

from sqlalchemy import event  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repository import ProgressRepository  # noqa: E402
from conftest import ASGIClient, FakeProvider  # noqa: E402
from test_conversation import scripted  # noqa: E402

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def audio_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
    return out.getvalue()


class Recorder:
    def __init__(self, engine):
        self.current: dict | None = None
        self.rows: list[dict] = []

        @event.listens_for(engine, 'before_cursor_execute')
        def statement(conn, cursor, text, parameters, context, many):
            if self.current is None or text.lstrip().upper().startswith(('PRAGMA', 'BEGIN')):
                return
            kind = text.lstrip().split(None, 1)[0].upper()
            self.current['queries'] += 1
            self.current['writes'] += kind in ('INSERT', 'UPDATE', 'DELETE')
            key = (re.sub(r'\s+', ' ', text).strip(), repr(parameters))
            self.current['seen'][key] += 1
            self.current['sql'].append(re.sub(r'\s+', ' ', text).strip()[:160])
            self.current['shapes'].append(shape(text))
            txn = self.by_conn.get(id(conn))
            if txn is not None:
                txn['shapes'].append(shape(text))
                txn['writes'] += kind in ('INSERT', 'UPDATE', 'DELETE')

        self.by_conn: dict[int, dict] = {}

        @event.listens_for(engine, 'begin')
        def begin(conn):
            if self.current is not None:
                self.current['transactions'] += 1
                txn = {'shapes': [], 'writes': 0}
                self.by_conn[id(conn)] = txn
                self.current['txns'].append(txn)

        original = engine.pool.connect

        def connect(*args, **kwargs):
            if self.current is not None:
                self.current['checkouts'] += 1
            return original(*args, **kwargs)
        engine.pool.connect = connect

    def start(self):
        self.current = {'txns': [], 'shapes': [], 'queries': 0, 'writes': 0, 'transactions': 0, 'checkouts': 0, 'seen': Counter(), 'sql': []}

    def finish(self, label: str):
        row, self.current = self.current, None
        row['duplicates'] = sum(count - 1 for count in row['seen'].values() if count > 1)
        row['label'] = label
        del row['seen']
        row['rtt_postgres'] = row['checkouts'] + 2 * row['transactions'] + row['queries']
        row['phases'] = phase_model(row, label)
        self.rows.append(row)


def drive(client, recorder: Recorder, audio: bytes):
    original = client.request

    def request(method, path, **kwargs):
        recorder.start()
        try:
            return original(method, path, **kwargs)
        finally:
            recorder.finish(f"{method} {UUID.sub('{id}', path.split('?')[0])}")
    client.request = request

    def post(path, **kw):
        response = client.post(path, **kw)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])
        return response.json()

    def step(lesson, verb):
        return post(f"/api/lessons/{lesson['id']}/{verb}", json={'revision': lesson['revision']})

    def respond(lesson):
        uploaded = post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']}, files={'audio': ('response.wav', audio, 'audio/wav')})
        return step(uploaded, 'answer')

    # Page load reads the client makes before a lesson.
    for path in ('/api/profile', '/api/eligibility', '/api/plan', '/api/lessons/current'):
        client.get(path)
    client.post('/api/recognition/acknowledgment-audio', json={'cue': 'assessment'})
    lesson = post('/api/lessons/start')
    lesson = respond(lesson)                    # check-in answer
    lesson = step(lesson, 'heard')
    if lesson['phase'] == 'REVIEW':
        lesson = respond(lesson); lesson = step(lesson, 'heard')
    lesson = step(lesson, 'heard')              # transition
    lesson = step(lesson, 'exercise')           # generate the first exercise
    exercise = lesson['exercise']
    client.get(f"/api/exercises/{exercise['id']}/audio")
    post(f"/api/exercises/{exercise['id']}/conversation/listened")
    scripted(client, [{'inference': 'insufficient_evidence'}, {}])
    followup = None
    for _ in range(3):                          # summary, then up to two follow-ups
        client.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': followup['id'] if followup else 'summary'}, headers={'Accept': 'audio/*'})
        uploaded = post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('response.wav', audio, 'audio/wav')})
        client.post('/api/recognition/acknowledgment-audio', json={'cue': 'assessment'})
        result = post(f"/api/attempts/{uploaded['id']}/assess",
                      json={'text': uploaded['transcription']['text'], 'confirmed': True, 'followup_id': followup['id'] if followup else None})
        if result['state'] != 'AWAITING_FOLLOWUP':
            break
        followup = result['active_followup']
    client.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'}, headers={'Accept': 'audio/*'})
    post(f"/api/exercises/{exercise['id']}/conversation/ready")
    client.get(f"/api/exercises/{exercise['id']}/conversation")
    client.post('/api/telemetry/turn', json={'stages': {'assessment': 5}})


def shape(text: str) -> str:
    """'SELECT exercises', 'INSERT assessment_evidence', ...: the verb and the main table of a statement."""
    verb = text.split(None, 1)[0].upper()
    table = re.search(r'(?:FROM|INTO|UPDATE)\s+([a-z_]+)', text, re.I)
    return f"{verb} {table.group(1) if table else '?'}"


def histogram(rows: list[dict], route: str) -> Counter:
    """Average statement shapes per call of the route."""
    items = [r for r in rows if r['label'] == route]
    total = Counter(name for r in items for name in r['shapes'])
    return Counter({k: round(v / len(items), 1) for k, v in total.items()})


PHASES = ('now', 'P1 no ping on hot connections', 'P2 read-only autocommit', 'P3 batch evidence + drop prior-selects',
          'P4 per-request memo of immutable exercise rows', 'P5 state-only transitions')


def phase_model(row: dict, label: str) -> list[int]:
    """Postgres round trips after each cumulative phase. Assumptions are listed in docs/AUD-82-DB-ROUNDTRIP-DESIGN.md."""
    C, T, Q = row['checkouts'], row['transactions'], row['queries']
    ro = sum(1 for t in row['txns'] if t['shapes'] and t['writes'] == 0)
    rtt0 = C + 2 * T + Q
    rtt1 = rtt0 - C                                                  # P1: skip the ping on a hot connection
    rtt2 = rtt1 - 2 * ro                                             # P2: no BEGIN / end-of-transaction for read-only operations
    batched = 0
    for t in row['txns']:
        evidence = t['shapes'].count('INSERT assessment_evidence')
        batched += max(0, evidence - 1)                              # P3: one multi-row insert instead of one per unit
        if 'INSERT conversations' in t['shapes']:
            batched += t['shapes'].count('SELECT conversation_followups') + t['shapes'].count('SELECT conversation_turns')
    rtt3 = rtt2 - batched
    exercise_reads = row['shapes'].count('SELECT exercises')
    rtt4 = rtt3 - max(0, exercise_reads - 1)                         # P4: exercises rows are immutable within a request
    rtt5 = 5 if label.endswith('/conversation/ready') else rtt4      # P5: one SELECT + one UPDATE in one transaction (ping, BEGIN, 2 statements, COMMIT)
    return [rtt0, rtt1, rtt2, rtt3, rtt4, min(rtt5, rtt4)]


def summarize(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[row['label']].append(row)
    out = []
    for label, items in groups.items():
        n = len(items)
        out.append({'route': label, 'calls': n,
                    'queries_per_call': round(sum(r['queries'] for r in items) / n, 1), 'checkouts_per_call': round(sum(r['checkouts'] for r in items) / n, 1),
                    'transactions_per_call': round(sum(r['transactions'] for r in items) / n, 1), 'writes_per_call': round(sum(r['writes'] for r in items) / n, 1),
                    'duplicate_statements_per_call': round(sum(r['duplicates'] for r in items) / n, 1),
                    'postgres_round_trips_per_call': round(sum(r['rtt_postgres'] for r in items) / n, 1),
                    'queries_max': max(r['queries'] for r in items), 'checkouts_max': max(r['checkouts'] for r in items)})
        out[-1]['phases_per_call'] = [round(sum(r['phases'][i] for r in items) / n, 1) for i in range(len(PHASES))]
    return sorted(out, key=lambda r: -r['postgres_round_trips_per_call'] * r['calls'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path)
    parser.add_argument('--histogram', action='store_true', help='print statement shapes per call for the heaviest routes')
    parser.add_argument('--show-sql', default='', help='print the statement sequence of the heaviest call whose route contains this text')
    args = parser.parse_args()
    audio = audio_bytes()
    with tempfile.TemporaryDirectory() as directory:
        repo = ProgressRepository(Path(directory) / 'audli.sqlite3', 'local')
        profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
        provider = FakeProvider()

        async def speak(text, speech_rate=.9):
            return audio
        provider.speak = speak
        client = ASGIClient(create_app(Settings(data_dir=Path(directory), _env_file=None), provider, repo), provider)
        recorder = Recorder(repo.engine)
        drive(client, recorder, audio)
        repo.close()
    report = summarize(recorder.rows)
    totals = {k: sum(r[k] for r in recorder.rows) for k in ('queries', 'checkouts', 'transactions', 'duplicates', 'writes', 'rtt_postgres')}
    print(f"{'route':58s} {'n':>2s} {'Q':>5s} {'C':>5s} {'T':>5s} {'dup':>5s} {'PG RTT':>7s}")
    for r in report:
        print(f"{r['route']:58s} {r['calls']:2d} {r['queries_per_call']:5.1f} {r['checkouts_per_call']:5.1f} {r['transactions_per_call']:5.1f} "
              f"{r['duplicate_statements_per_call']:5.1f} {r['postgres_round_trips_per_call']:7.1f}")
    print('TOTAL', totals)
    print(f"\nPostgres round trips per call, cumulative phases ({', '.join(PHASES[1:])})")
    print(f"{'route':58s} " + ' '.join(f'{p[:2]:>6s}' for p in PHASES))
    for r in report:
        print(f"{r['route']:58s} " + ' '.join(f'{v:6.1f}' for v in r['phases_per_call']))
    print(f"{'ALL ROUTES (sum over calls)':58s} " + ' '.join(f"{sum(rr['phases_per_call'][i] * rr['calls'] for rr in report):6.0f}" for i in range(len(PHASES))))
    if args.histogram:
        for entry in report[:7]:
            print(f"\n{entry['route']}  (per call: {entry['queries_per_call']} statements, {entry['checkouts_per_call']} checkouts)")
            for name, count in histogram(recorder.rows, entry['route']).most_common(12):
                print(f"   {count:5.1f}  {name}")
    if args.show_sql:
        heavy = max((r for r in recorder.rows if args.show_sql in r['label']), key=lambda r: r['queries'])
        print(f"\n{heavy['label']}: {heavy['queries']} statements, {heavy['checkouts']} checkouts, {heavy['transactions']} transactions")
        for i, text in enumerate(heavy['sql'], 1):
            print(f"{i:3d} {text}")
    if args.out:
        args.out.write_text(json.dumps({'routes': report, 'totals': totals}, indent=1))


if __name__ == '__main__':
    main()
