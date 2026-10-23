"""Run the three arms over the corpus and build a numbers-only report.

A0  current production path: client detector -> upload -> gpt-4o-mini-transcribe (REST, logprob confidence gate)
A1  gpt-live-transcribe over the Realtime WebSocket, client detector + commit (several delay / endpointing settings)
A2  gpt-transcribe, committed turn, with its `languages` signal

Nothing here touches the app's scoring, lesson state or database. Every paid step is reserved against the $5 ledger.
Dual-path trust model: the REST text (A0) stands in for the server's authoritative transcript; streaming text is
never treated as authoritative. Streaming-only acceptance is evaluated separately and labelled as such.

    python run_experiment.py --env-file /path/.env --data ~/audli-realtime-experiment [--limit N] [--plan-only]
"""
import argparse
import asyncio
import io
import json
import os
import sys
import wave
from pathlib import Path
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from analysis import (agreement_gate, endpoint_outcome, estimated_lesson_cost, gate_metrics, languages_gate,  # noqa: E402
                      score, summarize_numbers)
from spend import PRICES, Ledger, audio_cost  # noqa: E402
from vad import ADAPTIVE, PRODUCTION, SHORT, VadConfig, detect_end  # noqa: E402

RATE = 24000
VADS = {cfg.name: cfg for cfg in (PRODUCTION, SHORT, ADAPTIVE)}
# (name, model, delay, vad config)
STREAM_ARMS = [
    ('A1-low-1800', 'gpt-live-transcribe', 'low', PRODUCTION),
    ('A1-minimal-1800', 'gpt-live-transcribe', 'minimal', PRODUCTION),
    ('A1-high-1800', 'gpt-live-transcribe', 'high', PRODUCTION),
    ('A1-low-1200', 'gpt-live-transcribe', 'low', SHORT),
    ('A1-low-adaptive', 'gpt-live-transcribe', 'low', ADAPTIVE),
    ('A2-gpt-transcribe-1800', 'gpt-transcribe', None, PRODUCTION),
]
PROMPT_HINT = 'A short spoken answer from a Vietnamese learner of English about an app update, a database migration and a release date.'
AGREEMENT_THRESHOLDS = (0.05, 0.10, 0.15, 0.20, 0.30)
MIC_OPEN_MINUTES_PER_LESSON = 4.0      # ASSUMPTION: replaced by measured mic-open time once telemetry has real lessons


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path)) as wav:
        assert wav.getframerate() == RATE and wav.getnchannels() == 1 and wav.getsampwidth() == 2
        return wav.readframes(wav.getnframes())


def to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE); wav.writeframes(pcm)
    return out.getvalue()


def plan_costs(samples: list[dict], pcms: dict[str, bytes]) -> dict:
    """Worst-case seconds and dollars per arm before anything is sent."""
    seconds = {'A0': 0.0}
    for sample in samples:
        ends = {detect_end(pcms[sample['id']], RATE, cfg)['end_ms'] or sample['duration_ms'] for cfg in VADS.values()}
        seconds['A0'] += sum(end / 1000 for end in ends)
        for name, _, _, cfg in STREAM_ARMS:
            vad = detect_end(pcms[sample['id']], RATE, cfg)
            seconds[name] = seconds.get(name, 0.0) + (vad['end_ms'] or sample['duration_ms']) / 1000
    dollars = {'A0': audio_cost('gpt-4o-mini-transcribe', seconds['A0'])}
    for name, model, _, _ in STREAM_ARMS:
        dollars[name] = audio_cost(model, seconds[name])
    return {'seconds': {k: round(v, 1) for k, v in seconds.items()}, 'usd': {k: round(v, 4) for k, v in dollars.items()},
            'total_usd': round(sum(dollars.values()), 4)}


async def run_all(samples, pcms, key, ledger, concurrency, only_arms=None):
    from app.config import Settings
    from app.services.openai_provider import OpenAIProvider
    from streaming import stream_turn
    provider = OpenAIProvider(Settings(_env_file=None, openai_api_key=key))
    semaphore = asyncio.Semaphore(concurrency)
    raw = {'a0': {}, 'stream': {name: {} for name, *_ in STREAM_ARMS}, 'vad': {name: {} for name in VADS}, 'errors': {}}
    for sample in samples:
        for name, cfg in VADS.items():
            raw['vad'][name][sample['id']] = detect_end(pcms[sample['id']], RATE, cfg)

    async def rest(sample, end_ms):
        label = f"{sample['id']}@{end_ms}"
        if label in raw['a0']:
            return
        async with semaphore:
            clip = to_wav(pcms[sample['id']][:end_ms * RATE // 1000 * 2])
            ledger.reserve('A0', audio_cost('gpt-4o-mini-transcribe', end_ms / 1000))
            started = perf_counter()
            try:
                result = await provider.transcribe(clip, 'response.wav')
                raw['a0'][label] = {'text': result.text, 'confidence': result.confidence, 'uncertainty': result.uncertainty,
                                    'wall_ms': round((perf_counter() - started) * 1000, 1), 'seconds': end_ms / 1000}
            except Exception as error:
                raw['errors'][f'A0:{label}'] = type(error).__name__
            ledger.record('A0-rest', audio_cost('gpt-4o-mini-transcribe', end_ms / 1000), PRICES['gpt-4o-mini-transcribe'][1])

    async def stream(name, model, delay, cfg, sample):
        async with semaphore:
            seconds = (raw['vad'][cfg.name][sample['id']]['end_ms'] or sample['duration_ms']) / 1000
            ledger.reserve(name, audio_cost(model, seconds))
            try:
                result = await stream_turn(pcms[sample['id']], RATE, api_key=key, model=model, vad_config=cfg, delay=delay,
                                           prompt=PROMPT_HINT if model == 'gpt-live-transcribe' else None)
                raw['stream'][name][sample['id']] = result
                spent = audio_cost(model, result['streamed_seconds'])
            except Exception as error:
                raw['errors'][f"{name}:{sample['id']}"] = type(error).__name__
                spent = audio_cost(model, seconds)
            ledger.record(name, spent, PRICES[model][1])

    tasks = []
    for sample in samples:
        for name, cfg in VADS.items():
            end = raw['vad'][name][sample['id']]['end_ms'] or sample['duration_ms']
            tasks.append(rest(sample, end))
        for arm in STREAM_ARMS:
            if only_arms is None or arm[0] in only_arms:
                tasks.append(stream(*arm, sample))
    await asyncio.gather(*tasks)
    return raw


def build_report(samples: list[dict], raw: dict, ledger_summary: dict | None = None) -> dict:
    """Numbers only. Pure function of the manifest and the raw results (unit-tested)."""
    by_id = {s['id']: s for s in samples}
    speech = [s for s in samples if s['expect_speech']]
    silent = [s for s in samples if not s['expect_speech']]

    def a0_for(sample_id, vad_name):
        end = raw['vad'][vad_name][sample_id]['end_ms'] or by_id[sample_id]['duration_ms']
        return raw['a0'].get(f'{sample_id}@{end}'), end

    report: dict = {'clips': {'speech': len(speech), 'nonspeech': len(silent)}, 'arms': {}, 'endpointing': {}, 'safeguards': {}, 'cost': {}}
    # --- endpointing (free: a property of the detector and the audio) --------------------------------------------
    for vad_name in VADS:
        outcomes = {}
        for sample in speech:
            outcomes.setdefault(sample['category'], []).append(endpoint_outcome(raw['vad'][vad_name][sample['id']], sample['last_voice_ms']))
        every = [o for group in outcomes.values() for o in group]
        waits = [raw['vad'][vad_name][s['id']]['end_ms'] - s['last_voice_ms'] for s in speech if raw['vad'][vad_name][s['id']]['ended']
                 and raw['vad'][vad_name][s['id']]['end_ms'] >= s['last_voice_ms']]
        report['endpointing'][vad_name] = {
            'cut_off_rate': round(every.count('cut_off') / len(every), 4), 'cut_off': every.count('cut_off'), 'clips': len(every),
            'cut_off_by_category': {c: group.count('cut_off') for c, group in sorted(outcomes.items())},
            'wait_after_last_voice_ms': summarize_numbers(waits)}
    # --- A0: production path ------------------------------------------------------------------------------------
    from app.models import Transcription
    from app.recognition import reliable_recognition
    for vad_name in VADS:
        rows = []
        for sample in speech:
            result, end = a0_for(sample['id'], vad_name)
            if result is None:
                continue
            scored = score(sample['reference'], result['text'], tuple(sample['must_contain']))
            transcription = Transcription(text=result['text'], confidence=result['confidence'], uncertainty=result['uncertainty'])
            rows.append({**scored, 'category': sample['category'], 'gate': reliable_recognition(transcription, 0.65),
                         'rest_ms': result['wall_ms'], 'speech_end_to_final_ms': (end - sample['last_voice_ms']) + result['wall_ms'],
                         'cut_off': end < sample['last_voice_ms']})
        report['arms'][f'A0@{vad_name}'] = arm_block(rows, silent_hallucinations=count_words(
            [a0_for(s['id'], vad_name)[0] for s in silent]))
        report['safeguards'][f'A0@{vad_name}:production_confidence_gate'] = gate_metrics(rows, 'gate', 'wer')
    # --- A1 / A2 ------------------------------------------------------------------------------------------------
    for name, model, delay, cfg in STREAM_ARMS:
        results = raw['stream'].get(name, {})
        rows = []
        for sample in speech:
            result = results.get(sample['id'])
            if not result:
                continue
            scored = score(sample['reference'], result['text'], tuple(sample['must_contain']))
            rest, end = a0_for(sample['id'], cfg.name)
            row = {**scored, 'category': sample['category'], 'final_latency_ms': result['final_latency_ms'],
                   'first_partial_ms': result['first_partial_ms_after_speech_start'], 'partials': result['partials'],
                   'partials_before_commit': result['partials_before_commit'], 'timed_out': result['timed_out'],
                   'speech_end_to_final_ms': ((end - sample['last_voice_ms']) + result['final_latency_ms']) if result['final_latency_ms'] is not None else None,
                   'cut_off': end < sample['last_voice_ms'], 'errors': result['errors'],
                   'rest_text': rest['text'] if rest else None, 'stream_text': result['text'], 'languages': result['languages']}
            rows.append(row)
        block = arm_block(rows, silent_hallucinations=count_words([results.get(s['id']) and {'text': results[s['id']]['text']} for s in silent]))
        block['streamed_seconds'] = round(sum(results[s['id']]['streamed_seconds'] for s in samples if s['id'] in results), 1)
        block['timeouts'] = sum(bool(r['timed_out']) for r in rows)
        block['event_types_seen'] = sorted({t for r in results.values() for t in r['event_types']})
        report['arms'][name] = block
        if model == 'gpt-live-transcribe':
            for threshold in AGREEMENT_THRESHOLDS:
                for row in rows:
                    row['gate'] = bool(row['rest_text']) and agreement_gate(row['stream_text'], row['rest_text'], threshold)
                    row['wer_stream'] = row['wer']
                report['safeguards'][f'{name}:S1_agreement_le_{threshold}'] = gate_metrics(rows, 'gate', 'wer_stream')
        if model == 'gpt-transcribe':
            for row in rows:
                row['gate'] = languages_gate(row['languages'])
            report['safeguards'][f'{name}:S2_languages_signal'] = gate_metrics(rows, 'gate', 'wer')
            report['safeguards'][f'{name}:S2_languages_signal']['empty_languages'] = sum(not r['languages'] for r in rows)
    # --- cost ---------------------------------------------------------------------------------------------------
    for name, model, *_ in STREAM_ARMS:
        report['cost'][name] = {'usd_per_minute': PRICES[model][0], 'basis': PRICES[model][1],
                                'estimated_usd_per_lesson_mic_open': estimated_lesson_cost(PRICES[model][0], MIC_OPEN_MINUTES_PER_LESSON),
                                'estimated_usd_per_lesson_streaming_whole_10_min': estimated_lesson_cost(PRICES[model][0], 10.0)}
    report['cost']['A0'] = {'usd_per_minute': PRICES['gpt-4o-mini-transcribe'][0], 'basis': PRICES['gpt-4o-mini-transcribe'][1],
                            'estimated_usd_per_lesson_mic_open': estimated_lesson_cost(PRICES['gpt-4o-mini-transcribe'][0], MIC_OPEN_MINUTES_PER_LESSON)}
    report['cost']['assumption'] = f'{MIC_OPEN_MINUTES_PER_LESSON} min of open microphone per lesson (to be replaced with measured data)'
    if ledger_summary:
        report['spend'] = ledger_summary
    report['errors'] = raw.get('errors', {})
    return report


def count_words(results) -> dict:
    texts = [r['text'] for r in results if r and r.get('text')]
    return {'clips': len(results), 'with_text': len(texts), 'max_words': max((len(t.split()) for t in texts), default=0)}


def arm_block(rows: list[dict], silent_hallucinations: dict) -> dict:
    def numbers(key, subset=rows):
        return summarize_numbers([r[key] for r in subset if r.get(key) is not None])
    categories = sorted({r['category'] for r in rows})
    required = [r['kept_required'] for r in rows if r.get('kept_required') is not None]
    return {'clips': len(rows), 'wer': numbers('wer'), 'wer_by_category': {c: numbers('wer', [r for r in rows if r['category'] == c]) for c in categories},
            'self_correction_kept_rate': round(sum(required) / len(required), 4) if required else None, 'cut_off_clips': sum(r.get('cut_off', False) for r in rows),
            'speech_end_to_final_ms': numbers('speech_end_to_final_ms'), 'final_latency_ms_after_commit': numbers('final_latency_ms'),
            'rest_request_ms': numbers('rest_ms'), 'first_partial_ms_after_speech_start': numbers('first_partial_ms'),
            'partials_before_commit': numbers('partials_before_commit'), 'nonspeech': silent_hallucinations}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--data', type=Path, default=Path.home() / 'audli-realtime-experiment')
    parser.add_argument('--limit', type=int, default=0, help='only the first N speech clips (smoke test)')
    parser.add_argument('--arms', default='', help='comma list of stream arm names (default all)')
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--report', type=Path, default=None)
    args = parser.parse_args()
    manifest = json.loads((args.data / 'manifest.json').read_text())
    samples = manifest['samples']
    if args.limit:
        samples = [s for s in samples if s['expect_speech']][:args.limit] + [s for s in samples if not s['expect_speech']][:1]
    pcms = {s['id']: read_pcm(args.data / s['audio']) for s in samples}
    only = set(filter(None, args.arms.split(','))) or None
    costs = plan_costs(samples, pcms)
    ledger = Ledger(args.data / 'ledger.json')
    print(json.dumps({'plan': costs, 'ledger': ledger.summary()}, indent=1))
    if args.plan_only:
        return
    ledger.reserve('run-total', costs['total_usd'] if only is None else costs['total_usd'] * len(only) / len(STREAM_ARMS))
    from corpus import load_key
    load_key(args.env_file)
    raw = asyncio.run(run_all(samples, pcms, os.environ['OPENAI_API_KEY'], ledger, args.concurrency, only))
    (args.data / 'raw_results.json').write_text(json.dumps(raw))
    report = build_report(samples, raw, Ledger(args.data / 'ledger.json').summary())
    report['concurrency'] = args.concurrency
    out = args.report or (args.data / 'report.json')
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report['arms'], indent=1)[:6000])


if __name__ == '__main__':
    main()
