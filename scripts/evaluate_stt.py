"""Offline speech-recognition evaluation for accented English (AUD-68 Phase 0).

Measures, for the CURRENT production transcription path (``OpenAIProvider.transcribe``) and the app's
own reliability gate (``reliable_recognition``): word error rate, confidence, gate pass rate, false-reliable /
false-unreliable rates, a threshold sweep and STT latency, per category / speaker / provenance.

Privacy: audio and manifests stay outside the repository. Reports contain numbers and fixed codes only;
transcripts are written only with ``--include-text`` (and then must be treated as consented research data).
Only ``consented`` or ``synthetic`` samples are accepted. Never point this at learner production audio.

    .venv/bin/python scripts/evaluate_stt.py --manifest ~/stt-eval/manifest.json --provider openai --out report.json
    .venv/bin/python scripts/evaluate_stt.py --manifest tests/fixtures/stt_eval/manifest.example.json --provider fake --dry-run
"""
import argparse
import asyncio
import json
import re
import statistics
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402
from app.models import Transcription  # noqa: E402
from app.recognition import reliable_recognition  # noqa: E402

PROVENANCE = ('consented', 'synthetic')
FALSE_RELIABLE_WER = 0.25     # accepted by the gate although a quarter of the words are wrong
FALSE_UNRELIABLE_WER = 0.10   # rejected by the gate although nearly all words are right
SWEEP = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
AUDIO_SUFFIXES = {'.wav', '.mp3', '.m4a', '.mp4', '.webm', '.ogg'}


def normalize(text: str) -> list[str]:
    text = text.lower().replace('’', "'")
    return re.sub(r"[^a-z0-9' ]+", ' ', text).split()


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        raise ValueError('Reference text is empty.')
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


def uncertainty_codes(transcription: Transcription) -> list[str]:
    codes = []
    for message in transcription.uncertainty:
        lowered = message.lower()
        codes.append('confidence_unavailable' if 'unavailable' in lowered else 'low_confidence' if 'uncertain' in lowered
                     else 'too_short' if 'too little' in lowered else 'other')
    return sorted(set(codes))


def load_manifest(path: Path, prompts_path: Path, max_bytes: int) -> list[dict]:
    manifest = json.loads(path.read_text())
    prompts = {p['id']: p for p in json.loads(prompts_path.read_text())['prompts']}
    root, samples, seen = path.resolve().parent, [], set()
    for raw in manifest.get('samples', []):
        sid = raw.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', sid) or sid in seen:
            raise ValueError(f'Invalid or duplicate sample id: {sid!r}')
        seen.add(sid)
        if raw.get('provenance') not in PROVENANCE:
            raise ValueError(f'{sid}: provenance must be one of {PROVENANCE}; learner production audio is not allowed.')
        if raw['provenance'] == 'consented' and not raw.get('consent_record'):
            raise ValueError(f'{sid}: consented samples require a consent_record reference.')
        if raw['provenance'] == 'synthetic' and not raw.get('synthetic_source'):
            raise ValueError(f'{sid}: synthetic samples must name their synthetic_source.')
        if raw.get('prompt_id') not in prompts:
            raise ValueError(f'{sid}: unknown prompt_id {raw.get("prompt_id")!r}.')
        audio = (root / raw.get('audio', '')).resolve()
        if root not in audio.parents or audio.suffix.lower() not in AUDIO_SUFFIXES:
            raise ValueError(f'{sid}: audio must be a supported file inside the manifest directory.')
        samples.append({**raw, 'prompt': prompts[raw['prompt_id']], '_audio': audio})
    if not samples:
        raise ValueError('Manifest has no samples.')
    return samples


def summarize(rows: list[dict], minimum: float) -> dict:
    # Non-speech clips measure hallucination; they must not dilute speech accuracy or pass rates.
    silent = [r for r in rows if not r['expect_speech']]
    rows = [r for r in rows if r['expect_speech']]
    def block(items):
        scored = [r for r in items if r['wer'] is not None]
        wers = sorted(r['wer'] for r in scored)
        lat = sorted(r['stt_seconds'] for r in items)
        n = len(items)
        reliable = [r for r in items if r['gate_reliable']]
        return {
            'n': n,
            'wer_mean': round(statistics.fmean(wers), 4) if wers else None,
            'wer_median': round(statistics.median(wers), 4) if wers else None,
            'wer_p90': wers[min(len(wers) - 1, int(.9 * len(wers)))] if wers else None,
            'gate_pass_rate': round(len(reliable) / n, 4) if n else None,
            'false_reliable_rate': round(sum(r['wer'] > FALSE_RELIABLE_WER for r in reliable if r['wer'] is not None) / len(reliable), 4) if reliable else None,
            'false_unreliable_rate': round(sum(r['wer'] <= FALSE_UNRELIABLE_WER for r in items if not r['gate_reliable'] and r['wer'] is not None) / max(1, n - len(reliable)), 4) if n > len(reliable) else None,
            'confidence_missing': sum(r['confidence'] is None for r in items),
            'uncertainty_codes': {c: sum(c in r['uncertainty'] for r in items) for c in sorted({c for r in items for c in r['uncertainty']})},
            'stt_seconds_p50': round(statistics.median(lat), 3) if lat else None,
            'stt_seconds_p90': round(lat[min(len(lat) - 1, int(.9 * len(lat)))], 3) if lat else None,
        }
    def by(key):
        groups = {}
        for r in rows:
            groups.setdefault(r[key], []).append(r)
        return {k: block(v) for k, v in sorted(groups.items())}
    sweep = []
    for t in SWEEP:
        accepted = [r for r in rows if r['confidence'] is not None and r['confidence'] >= t and not r['uncertainty_other_than_confidence']]
        sweep.append({'min_confidence': t, 'pass_rate': round(len(accepted) / len(rows), 4),
                      'false_reliable_rate': round(sum(r['wer'] > FALSE_RELIABLE_WER for r in accepted if r['wer'] is not None) / len(accepted), 4) if accepted else None})
    nonspeech = {'n': len(silent), 'text_returned': sum(r['returned_words'] > 0 for r in silent),
                 'gate_false_accepts': sum(r['gate_reliable'] and r['returned_words'] > 0 for r in silent),
                 'returned_words_max': max((r['returned_words'] for r in silent), default=0)} if silent else None
    return {'gate_min_confidence': minimum, 'nonspeech': nonspeech, 'overall': block(rows), 'by_category': by('category'),
            'by_provenance': by('provenance'), 'by_speaker': by('speaker'), 'threshold_sweep': sweep}


async def run(samples, provider, settings, include_text):
    rows = []
    for sample in samples:  # sequential on purpose: latency must not include self-inflicted queueing
        data = sample['_audio'].read_bytes()
        if len(data) > settings.max_audio_bytes:
            raise ValueError(f'{sample["id"]}: audio exceeds the production size limit.')
        started = perf_counter()
        result = Transcription.model_validate(await provider.transcribe(data, sample['_audio'].name))
        seconds = perf_counter() - started
        codes = uncertainty_codes(result)
        row = {'id': sample['id'], 'prompt_id': sample['prompt_id'], 'category': sample['prompt']['category'],
               'speaker': sample['speaker'], 'provenance': sample['provenance'],
               'wer': None if sample['prompt'].get('expect_speech') is False
                      else round(wer(sample['prompt']['text'], result.text), 4) if result.text.strip() else 1.0,
               'expect_speech': sample['prompt'].get('expect_speech') is not False,
               'returned_words': len(normalize(result.text)),
               'confidence': None if result.confidence is None else round(result.confidence, 4),
               'uncertainty': codes, 'uncertainty_other_than_confidence': any(c in ('too_short', 'other') for c in codes),
               'gate_reliable': reliable_recognition(result, settings.min_transcription_confidence),
               'stt_seconds': round(seconds, 3)}
        if include_text:
            row['reference'], row['hypothesis'] = sample['prompt']['text'], result.text
        rows.append(row)
    return rows


class FakeProvider:
    """Deterministic plumbing check only: perturbs the reference. Says nothing about real accuracy."""
    def __init__(self, prompts):
        self.prompts = {p['id']: p['text'] for p in prompts}
        self.calls = 0

    async def transcribe(self, audio, filename):
        self.calls += 1
        prompt_id = filename.rsplit('-', 1)[-1].split('.')[0]
        text = self.prompts.get(prompt_id, 'unknown') or 'Thank you.'   # empty reference = non-speech: simulate a hallucination
        words = text.split()
        if self.calls % 4 == 0 and len(words) > 3:
            words[1] = 'zzz'
        return Transcription(text=' '.join(words), confidence=.9 if self.calls % 5 else .4,
                             uncertainty=[] if self.calls % 5 else ['Speech recognition was uncertain; please verify or record again.'])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--prompts', type=Path, default=ROOT / 'tests/fixtures/stt_eval/prompts.vi-en.json')
    ap.add_argument('--provider', choices=['openai', 'fake'], default='openai')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--include-text', action='store_true', help='Write reference/hypothesis text (consented research data only).')
    ap.add_argument('--dry-run', action='store_true', help='Validate the manifest and exit without calling a provider.')
    args = ap.parse_args(argv)
    settings = Settings(_env_file=None) if args.provider == 'fake' else Settings()
    samples = load_manifest(args.manifest, args.prompts, settings.max_audio_bytes)
    if args.dry_run:
        print(json.dumps({'valid': True, 'samples': len(samples)}))
        return 0
    if args.provider == 'fake':
        provider = FakeProvider(json.loads(args.prompts.read_text())['prompts'])
    else:
        from app.services.openai_provider import OpenAIProvider
        provider = OpenAIProvider(settings)
    rows = asyncio.run(run(samples, provider, settings, args.include_text))
    report = {'version': 1, 'provider': args.provider, 'transcription_model': settings.transcription_model if args.provider == 'openai' else 'fake',
              'contains_transcripts': args.include_text, 'plumbing_only': args.provider == 'fake',
              'summary': summarize(rows, settings.min_transcription_confidence), 'samples': rows}
    text = json.dumps(report, indent=1)
    args.out.write_text(text) if args.out else print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
