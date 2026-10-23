"""AUD-82 realtime-transcription experiment: pure logic only (no network, no provider calls, no audio files)."""
import json
import random
import struct
import sys
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).resolve().parents[1] / 'experiments' / 'realtime-transcription'
sys.path.insert(0, str(EXPERIMENT))

import analysis  # noqa: E402
import run_experiment  # noqa: E402
import spend  # noqa: E402
import streaming  # noqa: E402
import vad  # noqa: E402

RATE = 24000


def silence(ms, rng=random.Random(1)):
    return b''.join(struct.pack('<h', int(rng.gauss(0, 100))) for _ in range(RATE * ms // 1000))


def tone(ms, amplitude=5000):
    import math
    return b''.join(struct.pack('<h', int(amplitude * math.sin(2 * math.pi * 300 * i / RATE))) for i in range(RATE * ms // 1000))


def test_detector_waits_the_window_after_the_last_voice_like_production():
    pcm = silence(400) + tone(3000) + silence(4000)
    result = vad.detect_end(pcm, RATE, vad.PRODUCTION)
    assert result['ended'] and result['last_voice_ms'] == 3400
    assert result['end_ms'] == 3400 + 1800
    assert vad.detect_end(pcm, RATE, vad.SHORT)['end_ms'] == 3400 + 1200


def test_detector_never_ends_a_turn_before_two_seconds_and_gives_up_on_silence():
    short = silence(200) + tone(500) + silence(4000)
    assert vad.detect_end(short, RATE, vad.SHORT)['end_ms'] == 2000
    nothing = vad.detect_end(silence(16000), RATE, vad.PRODUCTION)
    assert nothing['ended'] and nothing['end_ms'] == 15000 and nothing['voiced_ms'] == 0


def test_a_long_mid_answer_pause_cuts_off_fixed_windows_and_adaptive_only_helps_the_second_time():
    pcm = silence(400) + tone(1500) + silence(2000) + tone(1500) + silence(2000) + tone(1000) + silence(5000)
    last_voice = 400 + 1500 + 2000 + 1500 + 2000 + 1000
    assert vad.detect_end(pcm, RATE, vad.PRODUCTION)['end_ms'] == 400 + 1500 + 1800     # first 2 s pause is cut off
    first_cut = vad.detect_end(pcm, RATE, vad.ADAPTIVE)['end_ms']
    assert first_cut == 400 + 1500 + 1200                                                # it cannot know about the first pause
    # Pauses of 1.1 s then 1.3 s: a fixed 1.2 s window cuts at the second; the pause-aware window has learned 1.1 s x 1.25 = 1.375 s.
    pcm2 = silence(400) + tone(1000) + silence(1100) + tone(1000) + silence(1300) + tone(1000) + silence(5000)
    assert vad.detect_end(pcm2, RATE, vad.SHORT)['end_ms'] == 400 + 1000 + 1100 + 1000 + 1200
    adaptive = vad.detect_end(pcm2, RATE, vad.ADAPTIVE)
    assert 7400 <= adaptive['end_ms'] <= 7550 and 1600 <= adaptive['window_ms'] <= 1700            # survived pause #2, ended 1.25 x 1.3 s after the last voice
    assert last_voice > 0


def test_ledger_refuses_before_spending_and_keeps_the_pilot_reserve(tmp_path):
    ledger = spend.Ledger(tmp_path / 'ledger.json')
    assert ledger.limit == 4.5 and ledger.remaining('synthetic') == 2.0 and ledger.remaining('pilot') == 4.5
    ledger.reserve('ok', 1.99)
    ledger.record('a', 1.5, 'test')
    with pytest.raises(spend.BudgetExceeded):
        ledger.reserve('too much', 0.6)
    ledger.reserve('pilot work may use the reserve', 2.9, phase='pilot')
    again = spend.Ledger(tmp_path / 'ledger.json')           # persisted
    assert again.spent == 1.5 and again.summary()['by_item'] == {'a': 1.5}
    with pytest.raises(spend.BudgetExceeded):
        again.reserve('negative', -1)


def test_prices_have_provenance_and_the_cost_math_is_per_minute():
    assert spend.audio_cost('gpt-live-transcribe', 60) == pytest.approx(0.017)
    assert all(basis for _, basis in spend.PRICES.values())


def test_scoring_ignores_fillers_and_checks_self_corrections():
    assert analysis.score('The, um, the update, uh, removed a field.', 'The the update removed a field')['wer'] == 0
    assert analysis.score('Moved to Thursday, no, I mean Friday.', 'Moved to Thursday no I mean Friday', ('friday',))['kept_required'] is True
    assert analysis.score('Moved to Thursday, no, I mean Friday.', 'Moved to Thursday', ('friday',))['kept_required'] is False
    assert analysis.endpoint_outcome({'ended': True, 'end_ms': 3000}, 3400) == 'cut_off'
    assert analysis.endpoint_outcome({'ended': True, 'end_ms': 5200}, 3400) == 'ok'


def test_gate_metrics_count_false_reliable_and_false_unreliable():
    rows = [{'g': True, 'w': 0.0}, {'g': True, 'w': 0.5}, {'g': False, 'w': 0.05}, {'g': False, 'w': 0.6}]
    m = analysis.gate_metrics(rows, 'g', 'w')
    assert (m['accepted'], m['false_reliable'], m['false_unreliable']) == (2, 1, 1)
    assert m['false_reliable_rate'] == 0.5 and m['false_unreliable_rate'] == 0.5


def test_agreement_and_language_safeguards():
    assert analysis.agreement_gate('the team rolled back the update', 'The team rolled back the update.', 0.1)
    assert not analysis.agreement_gate('the team rolled back the update', 'a different sentence entirely', 0.2)
    assert not analysis.agreement_gate('yes', 'yes', 0.1)                                 # too short to trust
    assert analysis.languages_gate([{'code': 'en'}]) and not analysis.languages_gate([]) and not analysis.languages_gate([{'code': 'fr'}])


def test_session_update_matches_the_documented_shape():
    live = streaming.session_update('gpt-live-transcribe', languages=('en',), prompt='p', keywords=('k',), delay='low')
    t = live['session']['audio']['input']
    assert live['session']['type'] == 'transcription' and t['format'] == {'type': 'audio/pcm', 'rate': 24000}
    assert t['turn_detection'] is None
    assert t['transcription'] == {'model': 'gpt-live-transcribe', 'languages': ['en'], 'prompt': 'p', 'keywords': ['k'], 'delay': 'low'}
    plain = streaming.session_update('gpt-transcribe', languages=('en',), delay='low')['session']['audio']['input']['transcription']
    assert plain == {'model': 'gpt-transcribe'}                                         # hints are documented for gpt-live-transcribe only
    assert 'language' not in t['transcription']


def test_server_events_are_reduced_to_kind_and_text():
    delta = streaming.parse_event(json.dumps({'type': streaming.DELTA, 'item_id': 'i', 'delta': 'Hel'}))
    done = streaming.parse_event(json.dumps({'type': streaming.COMPLETED, 'item_id': 'i', 'transcript': 'Hello', 'languages': [{'code': 'en'}]}))
    error = streaming.parse_event(json.dumps({'type': 'error', 'error': {'code': 'invalid_value', 'message': 'echoes user data'}}))
    other = streaming.parse_event(json.dumps({'type': 'session.updated', 'session': {'secret': 'x'}}))
    assert (delta['kind'], delta['text']) == ('delta', 'Hel') and (done['kind'], done['text'], done['languages']) == ('completed', 'Hello', [{'code': 'en'}])
    assert error['kind'] == 'error' and error['error_code'] == 'invalid_value' and 'message' not in error
    assert other['kind'] == 'other' and other['text'] is None


def _fabricated():
    samples = [{'id': 'P-S01', 'category': 'S', 'kind': 'S', 'reference': 'SECRET-REFERENCE-TEXT here now', 'must_contain': [], 'expect_speech': True,
                'first_voice_ms': 400, 'last_voice_ms': 2000, 'duration_ms': 8000},
               {'id': 'H07', 'category': 'H', 'kind': 'self_correction', 'reference': 'moved to thursday no i mean friday', 'must_contain': ['friday'],
                'expect_speech': True, 'first_voice_ms': 400, 'last_voice_ms': 3000, 'duration_ms': 9000},
               {'id': 'Z-tone', 'category': 'Z', 'kind': 'tone', 'reference': '', 'must_contain': [], 'expect_speech': False,
                'first_voice_ms': 400, 'last_voice_ms': 3400, 'duration_ms': 8000}]
    vad_out = {name: {s['id']: {'ended': True, 'end_ms': s['last_voice_ms'] + w, 'last_voice_ms': s['last_voice_ms'], 'first_voice_ms': 400,
                                'voiced_ms': 900, 'window_ms': w, 'longest_pause_ms': 0} for s in samples}
               for name, w in (('production-1800', 1800), ('short-1200', 1200), ('adaptive-1200', 1200))}
    raw = {'vad': vad_out, 'a0': {}, 'stream': {}, 'errors': {}}
    for name, v in vad_out.items():
        for s in samples:
            text = '' if not s['expect_speech'] else s['reference'].replace('SECRET-REFERENCE-TEXT', 'secret reference text')
            raw['a0'][f"{s['id']}@{v[s['id']]['end_ms']}"] = {'text': text, 'confidence': 0.9 if text else None, 'uncertainty': [], 'wall_ms': 1500.0, 'seconds': 3.0}
    for arm, model, *_ in run_experiment.STREAM_ARMS:
        raw['stream'][arm] = {s['id']: {'text': s['reference'] if s['expect_speech'] else '', 'languages': [{'code': 'en'}] if 'A2' in arm else None,
                                        'streamed_seconds': 3.0, 'final_latency_ms': 600.0, 'first_partial_ms_after_speech_start': 700.0,
                                        'partials': 5, 'partials_before_commit': 5, 'timed_out': False, 'errors': [], 'event_types': {'x': 1}} for s in samples}
    return samples, raw


def test_report_is_numbers_only_and_complete():
    samples, raw = _fabricated()
    report = run_experiment.build_report(samples, raw)
    text = json.dumps(report)
    assert 'SECRET-REFERENCE' not in text and 'secret reference' not in text and 'thursday' not in text.lower()
    assert report['clips'] == {'speech': 2, 'nonspeech': 1}
    assert set(report['endpointing']) == {'production-1800', 'short-1200', 'adaptive-1200'}
    assert report['arms']['A1-low-1800']['speech_end_to_final_ms']['p50'] == 1800 + 600
    assert report['arms']['A0@production-1800']['speech_end_to_final_ms']['p50'] == 1800 + 1500
    assert any(key.startswith('A1-low-1800:S1_agreement') for key in report['safeguards'])
    assert 'A2-gpt-transcribe-1800:S2_languages_signal' in report['safeguards']
    assert report['cost']['A1-low-1800']['usd_per_minute'] == 0.017


def test_the_experiment_directory_holds_no_audio_or_secrets():
    banned = {'.wav', '.mp3', '.m4a', '.webm', '.ogg', '.pcm'}
    for path in EXPERIMENT.rglob('*'):
        if path.is_file():
            assert path.suffix.lower() not in banned, path
            if path.suffix in {'.py', '.js', '.html', '.md', '.json', '.txt'}:
                content = path.read_text()
                assert 'sk-proj-' not in content and 'sk-live' not in content and 'ek_' not in content, path


def test_two_ledger_instances_do_not_lose_each_others_charges(tmp_path):
    first, second = spend.Ledger(tmp_path / 'ledger.json'), spend.Ledger(tmp_path / 'ledger.json')
    first.record('a', 0.1, 'x')
    second.record('b', 0.2, 'y')          # `second` was loaded before `first` wrote
    first.record('c', 0.3, 'z')
    assert spend.Ledger(tmp_path / 'ledger.json').spent == pytest.approx(0.6)
