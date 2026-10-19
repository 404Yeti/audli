"""Small, controlled real-provider latency benchmark (AUD-68 Phase 0). Numbers only.

* Synthetic content only: no learner data, no production accounts, no database, no Supabase.
* Reads ONLY an allowlist of variable NAMES from --env-file; values are never printed or logged.
* Hard call caps keep cost to cents (STT<=20, TTS<=26, LLM exercise=1, assessments<=6).
* Audio is synthesized with the configured OpenAI voice, so it is a US-English *baseline*,
  NOT Vietnamese-accented speech. It says nothing about accented-learner accuracy.
* Sequential calls; no shared lock; this measures providers, not the app.

    .venv/bin/python scripts/benchmark_providers.py --env-file /path/to/.env --out docs/AUD-68-PROVIDER-BASELINE.json
"""
import argparse, asyncio, json, os, statistics, sys, tempfile
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ALLOWED = {'OPENAI_API_KEY', 'AUDLI_LLM_MODEL', 'AUDLI_TRANSCRIPTION_MODEL', 'AUDLI_SPEECH_MODEL', 'AUDLI_VOICE', 'AUDLI_MIN_TRANSCRIPTION_CONFIDENCE'}
STT_PROMPTS = ['R01', 'R02', 'R03', 'R04', 'R05', 'G01', 'G02', 'G03', 'N01', 'N02', 'S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10']
CAPS = {'stt': 24, 'tts_stt_inputs': 20, 'tts_feedback': 6, 'llm_exercise': 1, 'llm_assess': 6}
FEEDBACK = ('You caught the main idea and the cause. Next time, listen for the date the team chose. '
            'Let’s keep listening together.')
LEARNER_ANSWERS = [
    'The team launched an update but customers could not save appointments, so they rolled back and will test the migration first.',
    'The update was broken because a database change removed a field. They went back to the old version and postponed the release to Friday.',
    'The app update have problem and customer cannot save, so team go back old version and wait until Friday for test.',
    'Maya say the migration delete one field. They return the old update. Next release is Friday after more test.',
    'It was about an app. Something went wrong.',
    'They fixed the problem quickly and want better checks before the next release.',
]


def load_env(path: Path):
    for line in path.read_text().splitlines():
        name, sep, value = line.partition('=')
        name = name.strip()
        if sep and name in ALLOWED and value.strip():
            os.environ[name] = value.strip().strip('"\'')
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('OPENAI_API_KEY is not set in the env file.')


def nonspeech_wav(kind, seconds=3, rate=16000):
    import io, math, random, struct, wave
    rng = random.Random(7)
    amp = {'silence': 0, 'quiet': 60, 'noise': 1500, 'tone': 6000}[kind]
    frames = b''.join(struct.pack('<h', int(amp * math.sin(2 * math.pi * 1000 * i / rate)) if kind == 'tone'
                                  else int(rng.gauss(0, amp))) for i in range(seconds * rate))
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
    return out.getvalue()


def pct(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 3) if values else None


def stats(values):
    return {'n': len(values), 'min': round(min(values), 3), 'p50': round(statistics.median(values), 3), 'p90': pct(values, .9), 'max': round(max(values), 3)} if values else {'n': 0}


async def main(args):
    load_env(args.env_file)
    from app.config import Settings
    from app.audio import sanitize_generated_audio
    from app.models import ConversationTurn, LearnerProfile
    from app.services.openai_provider import OpenAIProvider
    from scripts import evaluate_stt as ev
    settings = Settings(_env_file=None, provider='openai')
    provider = OpenAIProvider(settings)
    prompts = {p['id']: p for p in json.loads(args.prompts.read_text())['prompts']}
    report = {'version': 1, 'harness': 'real OpenAI providers; synthetic content; sequential; US-English synthetic voice (NOT accented)',
              'models': {'llm': settings.llm_model, 'transcription': settings.transcription_model, 'speech': settings.speech_model, 'voice': settings.voice},
              'caps': CAPS, 'calls': dict.fromkeys(CAPS, 0)}
    with tempfile.TemporaryDirectory(prefix='audli-bench-') as directory:
        root = Path(directory)
        # 1. Synthetic inputs (TTS) ------------------------------------------------------------
        tts_input, samples = [], []
        for pid in STT_PROMPTS[:CAPS['tts_stt_inputs']]:
            started = perf_counter()
            audio = await provider.speak(prompts[pid]['text'], 1.0)
            tts_input.append(perf_counter() - started); report['calls']['tts_stt_inputs'] += 1
            (root / f'syn-{pid}.mp3').write_bytes(audio)
            samples.append({'id': f'syn-{pid}', 'prompt_id': pid, 'audio': f'syn-{pid}.mp3', 'speaker': 'syn-openai-' + settings.voice,
                            'provenance': 'synthetic', 'synthetic_source': f'openai {settings.speech_model} {settings.voice}'})
        for index, (label, kind) in enumerate([('silence', 'silence'), ('quiet', 'quiet'), ('noise', 'noise'), ('tone', 'tone')], 1):
            (root / f'syn-nonspeech-{label}.wav').write_bytes(nonspeech_wav(kind))
            samples.append({'id': f'syn-nonspeech-{label}', 'prompt_id': f'Z{index:02d}', 'audio': f'syn-nonspeech-{label}.wav',
                            'speaker': 'syn-nonspeech', 'provenance': 'synthetic', 'synthetic_source': f'stdlib generated {kind}'})
        (root / 'manifest.json').write_text(json.dumps({'samples': samples}))
        # 2. STT through the production transcribe path ------------------------------------------
        loaded = ev.load_manifest(root / 'manifest.json', args.prompts, settings.max_audio_bytes)
        rows = await ev.run(loaded, provider, settings, include_text=False)
        report['calls']['stt'] = len(rows)
        report['stt'] = {'latency_seconds': stats([r['stt_seconds'] for r in rows]), 'summary': ev.summarize(rows, settings.min_transcription_confidence),
                         'note': 'Synthetic US-English baseline. Accent accuracy is NOT measured here.'}
        # 3. LLM exercise generation + evidence assessment ------------------------------------------
        started = perf_counter()
        exercise = await provider.generate(LearnerProfile())
        report['llm_exercise_generation_seconds'] = round(perf_counter() - started, 3); report['calls']['llm_exercise'] = 1
        assess = []
        for i, answer in enumerate(LEARNER_ANSWERS[:CAPS['llm_assess']]):
            started = perf_counter()
            await provider.assess(exercise, [ConversationTurn(attempt_id=f'bench-{i}', text=answer)])
            assess.append(perf_counter() - started); report['calls']['llm_assess'] += 1
        report['llm_assessment_seconds'] = stats(assess)
        # 4. Feedback TTS + the production sanitization step ----------------------------------------
        synth, sanitize = [], []
        for _ in range(CAPS['tts_feedback']):
            started = perf_counter(); audio = await provider.speak(FEEDBACK, .9); synth.append(perf_counter() - started)
            started = perf_counter(); await sanitize_generated_audio(audio, 'mp3'); sanitize.append(perf_counter() - started)
            report['calls']['tts_feedback'] += 1
        report['feedback_tts_seconds'] = stats(synth); report['sanitization_seconds'] = stats(sanitize)
        report['tts_synthetic_input_seconds'] = stats(tts_input)
    # Composite estimate of the provider-bound portion of one turn (silence + app overhead excluded) ----
    p = lambda d: d.get('p50') or 0
    report['composite_provider_p50_seconds'] = round(report['stt']['latency_seconds']['p50'] + p(report['llm_assessment_seconds']) + p(report['feedback_tts_seconds']) + p(report['sanitization_seconds']), 3)
    report['composite_note'] = 'STT + assessment + feedback TTS + sanitization at each stage p50. Excludes 1.8 s silence window, upload/network, playback start. Small n; indicative only.'
    return report

ap = argparse.ArgumentParser()
ap.add_argument('--env-file', type=Path, required=True)
ap.add_argument('--prompts', type=Path, default=ROOT / 'tests/fixtures/stt_eval/prompts.vi-en.json')
ap.add_argument('--out', type=Path)
args = ap.parse_args()
result = asyncio.run(main(args))
text = json.dumps(result, indent=1)
args.out.write_text(text) if args.out else print(text)
