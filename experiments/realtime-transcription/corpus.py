"""Build the SYNTHETIC test corpus (no learner audio, no consented audio): TTS speech with scripted pauses.

Honest limit: TTS told to "sound Vietnamese" is a stress variety, NOT evidence about real Vietnamese-accented
learners. It validates plumbing, latency, endpointing and safeguard mechanics. Accent accuracy needs the consented
pilot (see CONSENT.md). Output stays OUTSIDE the repository (default ~/audli-realtime-experiment).

    python corpus.py --env-file /path/.env --out ~/audli-realtime-experiment
"""
import argparse
import asyncio
import io
import json
import os
import random
import struct
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[1]
from spend import Ledger, audio_cost  # noqa: E402
from vad import frames, THRESHOLD  # noqa: E402

RATE = 24000
LEAD_MS, TRAIL_MS = 400, 4500
NOISE_AMPLITUDE = 100            # about 0.003 RMS: below the 0.015 voice threshold, like a quiet room
VOICES = ('alloy', 'sage', 'shimmer')
STYLE = ('Speak English as a Vietnamese learner of English might: a noticeable Vietnamese accent, softened final consonants, '
         'even syllable timing, moderate pace. Say exactly the given words, including hesitation sounds, and add nothing.')
SYNTHETIC_SOURCE = 'openai gpt-4o-mini-tts, accent-style instructions (not a real Vietnamese speaker)'
PROMPT_CATEGORIES = ('R', 'G', 'S', 'N')


def load_key(path: Path) -> None:
    for line in path.read_text().splitlines():
        name, sep, value = line.partition('=')
        if sep and name.strip() == 'OPENAI_API_KEY' and value.strip():
            os.environ['OPENAI_API_KEY'] = value.strip().strip('"\'')
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('OPENAI_API_KEY missing in env file.')


def silence(ms: int, rng: random.Random) -> bytes:
    return b''.join(struct.pack('<h', int(rng.gauss(0, NOISE_AMPLITUDE))) for _ in range(RATE * ms // 1000))


def to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE); wav.writeframes(pcm)
    return out.getvalue()


def voiced_span(pcm: bytes) -> tuple[int | None, int | None]:
    voiced = [t for t, rms in frames(pcm, RATE) if rms >= THRESHOLD]
    return (voiced[0] - 50, voiced[-1]) if voiced else (None, None)


def nonspeech(kind: str, rng: random.Random) -> bytes:
    amplitude = {'silence': 0, 'quiet': 60, 'noise': 1500, 'tone': 6000}[kind]
    import math
    body = b''.join(struct.pack('<h', int(amplitude * math.sin(2 * math.pi * 1000 * i / RATE)) if kind == 'tone'
                                else int(rng.gauss(0, amplitude))) for i in range(RATE * 3))
    return silence(LEAD_MS, rng) + body + silence(TRAIL_MS, rng)


async def synthesize(client, ledger: Ledger, text: str, voice: str) -> bytes:
    estimate = audio_cost('tts-audio', max(2.0, len(text) / 12))
    ledger.reserve(f'tts:{voice}', estimate)
    result = await client.audio.speech.create(model='gpt-4o-mini-tts', voice=voice, input=text, instructions=STYLE, response_format='pcm')
    pcm = result.content
    ledger.record('tts-audio', audio_cost('tts-audio', len(pcm) / 2 / RATE), 'assumed $0.03/min for generated speech (2x typical)')
    return pcm


async def build(out: Path, env_file: Path, concurrency: int = 3) -> dict:
    load_key(env_file)
    from openai import AsyncOpenAI
    client = AsyncOpenAI(max_retries=2, timeout=60)
    ledger = Ledger(out / 'ledger.json')
    prompts = json.loads((ROOT / 'tests/fixtures/stt_eval/prompts.vi-en.json').read_text())['prompts']
    hesitation = json.loads((HERE / 'hesitation_set.json').read_text())['items']
    audio_dir = out / 'audio'
    audio_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    plan = []
    for index, prompt in enumerate([p for p in prompts if p['category'] in PROMPT_CATEGORIES]):
        plan.append({'id': 'P-' + prompt['id'], 'category': prompt['category'], 'kind': prompt['category'], 'voice': VOICES[index % 3],
                     'segments': [{'say': prompt['text']}], 'reference': prompt['text'], 'must_contain': []})
    for index, item in enumerate(hesitation):
        reference = ' '.join(s['say'] for s in item['segments'] if 'say' in s)
        plan.append({'id': item['id'], 'category': 'H', 'kind': item['kind'], 'voice': VOICES[index % 3], 'segments': item['segments'],
                     'reference': reference, 'must_contain': item.get('must_contain', [])})
    semaphore = asyncio.Semaphore(concurrency)

    async def make(entry):
        target = audio_dir / (entry['id'] + '.wav')
        if target.exists():
            pcm = wave.open(str(target)).readframes(10 ** 9)
        else:
            parts = [silence(LEAD_MS, rng)]
            async with semaphore:
                for segment in entry['segments']:
                    parts.append(silence(segment['pause_ms'], rng) if 'pause_ms' in segment
                                 else await synthesize(client, ledger, segment['say'], entry['voice']))
            parts.append(silence(TRAIL_MS, rng))
            pcm = b''.join(parts)
            target.write_bytes(to_wav(pcm))
        first, last = voiced_span(pcm)
        return {'id': entry['id'], 'category': entry['category'], 'kind': entry['kind'], 'reference': entry['reference'],
                'must_contain': entry['must_contain'], 'audio': f'audio/{entry["id"]}.wav', 'voice': entry['voice'],
                'provenance': 'synthetic', 'synthetic_source': SYNTHETIC_SOURCE, 'expect_speech': True,
                'first_voice_ms': first, 'last_voice_ms': last, 'duration_ms': len(pcm) * 1000 // (2 * RATE)}

    samples = list(await asyncio.gather(*(make(entry) for entry in plan)))
    for kind in ('silence', 'quiet', 'noise', 'tone'):
        pcm = nonspeech(kind, rng)
        (audio_dir / f'Z-{kind}.wav').write_bytes(to_wav(pcm))
        first, last = voiced_span(pcm)
        samples.append({'id': f'Z-{kind}', 'category': 'Z', 'kind': kind, 'reference': '', 'must_contain': [], 'audio': f'audio/Z-{kind}.wav',
                        'voice': None, 'provenance': 'synthetic', 'synthetic_source': 'locally generated non-speech', 'expect_speech': False,
                        'first_voice_ms': first, 'last_voice_ms': last, 'duration_ms': len(pcm) * 1000 // (2 * RATE)})
    manifest = {'version': 1, 'sample_rate': RATE, 'samples': samples}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return {'samples': len(samples), 'ledger': ledger.summary()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path.home() / 'audli-realtime-experiment')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(build(args.out, args.env_file)), indent=1))
