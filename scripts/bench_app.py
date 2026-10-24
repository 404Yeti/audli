"""AUD-87 benchmark server: the REAL FastAPI app, real PostgreSQL, real authentication middleware and account lease.
Only the external services are stubbed with fixed, production-like latencies: the AI/TTS providers and the Supabase Auth
HTTP call. Synthetic data only; never point it at production.

    AUDLI_BENCH_DATABASE_URL=postgresql://... python -m uvicorn --factory --app-dir scripts bench_app:build

Latencies (milliseconds, defaults from the 2026-10-24 production class): AUDLI_BENCH_AUTH_MS=300 (Supabase Auth),
TRANSCRIBE_MS=1100, ASSESS_MS=3500, SPEAK_MS=450, GENERATE_MS=2000. Other AUDLI_* settings are read from the environment as usual.
"""
import asyncio
import io
import math
import os
import struct
import tempfile
import wave
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.config import Settings
from app.evaluation import expected_units
from app.main import create_app
from app.models import EvidenceAssessment, Transcription
from app.repository import PostgresRepository
from app.services.demo import DemoProvider

QUESTIONS = {'main_idea': 'What was the speaker mainly talking about?', 'details': 'What happened after the update?',
             'vocabulary': "What did the speaker mean by 'rolled back'?", 'inference': 'Why do you think Maya chose to wait?'}


def seconds(name: str, default: float) -> float:
    return float(os.environ.get('AUDLI_BENCH_' + name, default)) / 1000


def learner_uuid(token: str) -> str:
    return str(uuid5(NAMESPACE_URL, 'audli-bench:' + token))


def wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
        handle.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
    return out.getvalue()


class BenchProvider(DemoProvider):
    """Deterministic: the first answer of an exercise leaves 'inference' unknown (one follow-up), the second completes it."""
    audio = wav()

    async def generate(self, profile):
        await asyncio.sleep(seconds('GENERATE_MS', 2000))
        return await super().generate(profile)

    async def speak(self, text, speech_rate=.9):
        await asyncio.sleep(seconds('SPEAK_MS', 450))
        return self.audio

    async def transcribe(self, audio, filename):
        await asyncio.sleep(seconds('TRANSCRIBE_MS', 1100))
        return Transcription(text='Team rolled back the update because the database migration removed a field.', confidence=.99, uncertainty=[])

    async def assess(self, exercise, turns):
        await asyncio.sleep(seconds('ASSESS_MS', 3500))
        statuses = {'inference': 'insufficient_evidence'} if len(turns) == 1 else {}
        return EvidenceAssessment(units=[{'dimension': dim, 'index': i, 'status': statuses.get(dim, 'demonstrated'),
            'evidence': '' if statuses.get(dim) == 'insufficient_evidence' else turns[-1].text,
            'question': QUESTIONS[dim] if statuses.get(dim) == 'insufficient_evidence' else ''}
            for dim, units in expected_units(exercise).items() for i in range(len(units))],
            feedback='You caught the main idea. Let’s keep listening for the reasons.', transcription_concern=False, concern_reason='')


async def supabase_stub(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(seconds('AUTH_MS', 300))
    token = request.headers.get('authorization', '').removeprefix('Bearer ')
    if not token.startswith('bench-'):
        return httpx.Response(401, json={'message': 'invalid'})
    return httpx.Response(200, json={'id': learner_uuid(token), 'email_confirmed_at': '2026-10-06T00:00:00Z', 'is_anonymous': False})


def build():
    url = os.environ['AUDLI_BENCH_DATABASE_URL']
    data = Path(tempfile.mkdtemp(prefix='audli-bench-'))
    settings = Settings(data_dir=data, provider='demo', auth_mode='supabase', supabase_url='https://bench.invalid',
                        supabase_publishable_key='sb_publishable_bench', allowed_origins='https://bench.invalid', persistence='postgres',
                        database_url=url, latency_aggregates=True, latency_summary_seconds=3600,
                        per_learner_locks=os.environ.get('AUDLI_PER_LEARNER_LOCKS', 'true') == 'true', _env_file=None)
    return create_app(settings, BenchProvider(), PostgresRepository(url, None), auth_client=httpx.AsyncClient(transport=httpx.MockTransport(supabase_stub)))
