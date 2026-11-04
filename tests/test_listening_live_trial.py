"""The live validation harness: it must be impossible to spend by accident, and it must stop at the approved ceiling.
Everything here runs against httpx.MockTransport. No key, no network, no paid call."""
import json
import wave
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from scripts import listening_live_trial as trial

FIXTURE = json.loads((Path(trial.FIXTURES) / 'pharmacy_a2' / 'lesson.json').read_text())


def approval(tmp_path, **change):
    data = {'matrix_sha256': trial.digest(), 'max_usd': 5.0, 'approved_by': 'owner', 'approved_at': '2026-11-04T00:00:00+00:00',
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), **change}
    path = tmp_path / f'approval-{uuid4().hex}.json'
    path.write_text(json.dumps(data))
    return str(path)


def test_plan_is_printed_without_any_call_and_fits_the_cap(capsys, monkeypatch):
    monkeypatch.setattr(trial, 'make_client', lambda *a, **k: (_ for _ in ()).throw(AssertionError('a plan must never build a client')))
    assert trial.main([]) == 0
    out = capsys.readouterr().out
    matrix, cases = trial.load()
    rows, total = trial.plan(matrix, cases)
    assert f'worst-case total US${total}' in out and total <= matrix['cap_usd'] <= trial.HARD_CAP_USD == 5.0
    assert {r['model'] for r in rows} >= {'gpt-6-luna', 'gpt-transcribe', 'gpt-6.1-terra'}
    assert sum(r['calls'] for r in rows) == 90 + 30 + 30 + 30 + 18 + 18 + 12 + 12


def test_cases_cover_every_category_for_both_lessons():
    _, cases = trial.load()
    seen = {(c['lesson'], c['category']) for c in cases['assessment']}
    assert seen == {(l, c) for l in ('restaurant_b1', 'pharmacy_a2') for c in ('correct', 'incorrect', 'incomplete', 'ambiguous', 'unintelligible')}
    for case in cases['assessment']:
        _, ids = trial.fixture_points(case['lesson'])
        assert set(case['allowed']) == set(ids)
        if case['category'] in ('incorrect', 'ambiguous', 'unintelligible'):
            assert not any('covered' in states for states in case['allowed'].values()), case['id']     # such answers may never be credited


def test_running_needs_an_approval_that_matches_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test-not-real')
    monkeypatch.setattr(trial, 'make_client', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no call without approval')))
    for argv, message in ((['--run'], '--approval FILE is required'), (['--run', '--approval', approval(tmp_path, matrix_sha256='0' * 64)], 'different matrix'),
                          (['--run', '--approval', approval(tmp_path, expires_at='2020-01-01T00:00:00+00:00')], 'expired'),
                          (['--run', '--approval', approval(tmp_path, max_usd=5.01)], 'at most 5.0'), (['--run', '--approval', approval(tmp_path, max_usd=0)], 'max_usd'),
                          (['--run', '--approval', approval(tmp_path, max_usd=0.50)], 'worst-case cost'), (['--run', '--approval', approval(tmp_path, approved_by='')], 'approved_by')):
        with pytest.raises(SystemExit, match=message):
            trial.main(argv)
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(SystemExit, match='OPENAI_API_KEY'):
        trial.main(['--run', '--approval', approval(tmp_path)])


def test_editing_the_matrix_invalidates_an_approval(tmp_path, monkeypatch):
    first = trial.digest()
    copy = tmp_path / 'matrix.json'
    copy.write_text(trial.MATRIX.read_text().replace('"repeats": 3', '"repeats": 9'))
    monkeypatch.setattr(trial, 'MATRIX', copy)
    assert trial.digest() != first


def test_the_ledger_stops_before_a_call_that_could_pass_the_ceiling():
    ledger = trial.Ledger(0.01)
    ledger.reserve(0.004)
    ledger.settle(0.004, 0.001)
    assert ledger.spent == pytest.approx(0.001) and ledger.calls == 1
    ledger.reserve(0.004)
    with pytest.raises(trial.BudgetExceeded):
        ledger.reserve(0.0061)


def handler(request):
    path, body = request.url.path, (json.loads(request.content) if request.headers.get('content-type', '').startswith('application/json') else {})
    if path.endswith('/audio/transcriptions'):
        return httpx.Response(200, json={'text': 'hello', 'languages': [], 'usage': {'type': 'duration', 'seconds': 8}})
    if path.endswith('/chat/completions'):
        ids = body['response_format']['json_schema']['schema']['properties']['points']['items']['properties']['id']['enum']
        content = json.dumps({'points': [{'id': i, 'state': 'missing', 'quote': None} for i in ids]})
        return httpx.Response(200, json={'id': 'c', 'object': 'chat.completion', 'created': 0, 'model': 'm', 'usage': {'prompt_tokens': 1500, 'completion_tokens': 100},
                                         'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]})
    fmt = body['text']['format']
    if fmt['type'] == 'json_schema':
        ids = fmt['schema']['properties']['points']['items']['properties']['id']['enum']
        text = json.dumps({'points': [{'id': i, 'state': 'missing', 'quote': None} for i in ids]})
    else:
        text = json.dumps({**FIXTURE, 'slug': 'generated-lesson', 'title': 'Generated'})
    return httpx.Response(200, json={'id': 'r', 'object': 'response', 'created_at': 0, 'status': 'completed', 'model': 'm', 'parallel_tool_calls': False, 'tool_choice': 'auto',
        'tools': [], 'usage': {'input_tokens': 1500, 'output_tokens': 300}, 'output': [{'type': 'message', 'id': 'm', 'role': 'assistant', 'status': 'completed',
        'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]})


def test_an_approved_run_uses_the_production_adapters_measures_cost_and_writes_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test-not-real')
    audio = tmp_path / 'audio'
    audio.mkdir()
    for name in ('a.wav', 'b.wav'):
        with wave.open(str(audio / name), 'wb') as out:
            out.setnchannels(1), out.setsampwidth(2), out.setframerate(16000), out.writeframes(b'\x00\x00' * 16000)
    (audio / 'manifest.json').write_text(json.dumps([{'file': 'a.wav', 'kind': 'noise'}, {'file': 'b.wav', 'kind': 'speech', 'keywords': ['restaurant']}]))
    seen = []

    def factory(usage):
        return httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r.url.path), handler(r))[1]), event_hooks={'response': [usage.hook]})
    out = tmp_path / 'results'
    assert trial.main(['--run', '--approval', approval(tmp_path), '--audio-dir', str(audio), '--out', str(out)], http_factory=factory) == 0
    report = json.loads(next(out.glob('*.json')).read_text())
    assert report['calls'] == len(seen) == 90 + 30 + 30 + 30 + 2 + 2 + 12 + 12 and 'stopped' not in report
    assert 0 < report['spent_usd_estimate'] < 1 and report['spent_usd_estimate'] < report['ceiling_usd']      # measured usage replaced the worst-case reservations
    a1 = report['rows']['A1']
    assert a1['of'] == 90 and a1['inconsistent_cases'] == 0 and a1['malformed'] == 0 and set(a1['by_category']) == {'correct', 'incorrect', 'incomplete', 'ambiguous', 'unintelligible'}
    assert a1['by_category']['incorrect'] == '18/18' and a1['by_category']['correct'] == '0/18'           # a model that credits nothing fails the "correct" cases, as it should
    assert report['rows']['G1']['valid'] == 12 and report['rows']['T1']['of'] == 2
    assert [r['ok'] for r in report['rows']['T1']['results']] == [True, False]
    raw = next(out.glob('*.json')).read_text()
    assert 'sk-test' not in raw and 'Authorization' not in raw


def test_a_run_that_would_pass_the_ceiling_stops_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test-not-real')
    matrix, cases = trial.load()
    total = trial.plan(matrix, cases)[1]
    monkeypatch.setattr(trial, 'plan', lambda m, c: ([], 0.0))           # let a tiny ceiling past the up-front check to prove the per-call guard
    out = tmp_path / 'results'
    trial.main(['--run', '--approval', approval(tmp_path, max_usd=0.01), '--out', str(out)],
               http_factory=lambda usage: httpx.Client(transport=httpx.MockTransport(handler), event_hooks={'response': [usage.hook]}))
    report = json.loads(next(out.glob('*.json')).read_text())
    assert 'could exceed the ceiling' in report['stopped'] and report['spent_usd_estimate'] <= 0.01 < total
