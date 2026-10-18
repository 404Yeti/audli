"""Application-overhead floor for one exercise turn (AUD-68 Phase 0).

Fake providers with ZERO delay, so every stage is OUR code: upload validation, persistence, deterministic
adaptation, TTS sanitization (real ffmpeg) and cache I/O. Add provider time on top for real latency.
Not a production measurement. Output is the numeric aggregate only.

    .venv/bin/python scripts/measure_app_overhead.py [--turns 20] > out.json
"""
import argparse, asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
tmp = tempfile.TemporaryDirectory(prefix='audli-overhead-bootstrap-')
os.environ.update({'AUDLI_ENVIRONMENT': 'development', 'AUDLI_PERSISTENCE': 'sqlite', 'AUDLI_DATABASE_URL': '',
                   'AUDLI_LEARNER_ID': '', 'AUDLI_ALLOWED_ORIGINS': '', 'AUDLI_DATA_DIR': tmp.name, 'AUDLI_AUTH_MODE': 'local'})
import httpx  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.telemetry import aggregator  # noqa: E402
from conftest import FakeProvider  # noqa: E402
from lock_concurrency import wav_bytes  # noqa: E402
from test_conversation import assessment  # noqa: E402


async def main(turns):
    provider = FakeProvider()
    async def speak(text, speech_rate=.9):
        return await provider.speech(None)
    async def assess(content, history):
        return assessment(content, history)
    provider.speak, provider.assess = speak, assess
    with tempfile.TemporaryDirectory(prefix='audli-overhead-') as directory:
        app = create_app(Settings(data_dir=Path(directory), provider='demo', latency_aggregates=True, _env_file=None), provider)
        aggregator.reset()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as c:
            for _ in range(turns):
                exercise = (await c.post('/api/exercises')).json()
                await c.post(f"/api/exercises/{exercise['id']}/conversation/listened")
                attempt = (await c.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('a.wav', wav_bytes(), 'audio/wav')})).json()
                state = (await c.post(f"/api/attempts/{attempt['id']}/assess", json={'text': attempt['transcription']['text'], 'confirmed': True, 'followup_id': None})).json()
                assert state['state'] == 'GIVING_FEEDBACK', state['state']
                assert (await c.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'})).status_code == 200
                await c.post(f"/api/exercises/{exercise['id']}/conversation/ready")
        app.state.repository.close()
    stages = aggregator.snapshot()['stages']
    keep = {k: v for k, v in stages.items() if k.startswith('http.POST') or k in ('turn.upload_validation', 'turn.transcription', 'turn.assessment_checkpoint_persistence', 'turn.adaptive_decision', 'ProgressRepository.complete_conversation', 'turn.tts_sanitization', 'SpeechService.coach_audio', 'turn.upload.lock_wait', 'turn.upload.lock_hold', 'turn.assessment.lock_hold', 'turn.coach_audio.lock_hold')}
    return {'harness': 'zero-delay fakes; real ffmpeg sanitization; SQLite; in-process ASGI; one learner', 'turns': turns, 'stages': keep}

ap = argparse.ArgumentParser(); ap.add_argument('--turns', type=int, default=20)
print(json.dumps(asyncio.run(main(ap.parse_args().turns)), indent=1))
