import asyncio
import io
import math
import struct
import wave
import os
import tempfile
import pytest
import httpx

# Isolate collection-time app startup too, before app.main imports its default app.
# Tests must never inherit production persistence/ownership from a shell or .env.
_bootstrap_data = tempfile.TemporaryDirectory(prefix='audli-test-bootstrap-')
os.environ.update({
    'AUDLI_ENVIRONMENT': 'development', 'AUDLI_PERSISTENCE': 'sqlite',
    'AUDLI_DATABASE_URL': '', 'AUDLI_LEARNER_ID': '',
    'AUDLI_ALLOWED_ORIGINS': '', 'AUDLI_DATA_DIR': _bootstrap_data.name,
    'AUDLI_AUTH_MODE': 'local', 'AUDLI_SUPABASE_URL': '', 'AUDLI_SUPABASE_PUBLISHABLE_KEY': '',
})

from app.config import Settings
from app.main import create_app
from app.models import LearnerProfile, Transcription, EvaluationJudgments
from app.evaluation import expected_units
from app.services.demo import DemoProvider

@pytest.fixture
def exercise():
    return asyncio.run(DemoProvider().generate(LearnerProfile()))

@pytest.fixture
def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2*math.pi*220*i/8000))) for i in range(24000)))
    return output.getvalue()

class FakeProvider(DemoProvider):
    fail = False
    concern = False
    uncertain = False
    evaluations = 0

    async def speech(self, exercise):
        if self.fail:
            raise ValueError('SECRET provider payload')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
            wav.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2*math.pi*220*i/8000))) for i in range(24000)))
        return output.getvalue()

    async def transcribe(self, audio, filename):
        return Transcription(text='Team rolled back the update because the database migration removed a field.', confidence=.2 if self.uncertain else .99,
            uncertainty=['Uncertain recognition'] if self.uncertain else [])

    async def evaluate(self, exercise, transcript):
        self.evaluations += 1
        return EvaluationJudgments(units=[{'dimension': d, 'index': i, 'status': 'understood', 'evidence': transcript}
            for d, units in expected_units(exercise).items() for i in range(len(units))],
            feedback='You understood the cause and solution.', transcription_concern=self.concern, concern_reason='unreliable' if self.concern else '')

class ASGIClient:
    # Execute requests on one thread; no localhost sockets or cross-thread wakeups.
    def __init__(self, app, provider):
        self.app, self.provider = app, provider

    def request(self, method, path, **kwargs):
        async def perform():
            async def poll_worker_results():
                # FileResponse uses AnyIO worker threads. Restricted environments can
                # lose their socket-based loop wakeup; a timer keeps completed work moving.
                while True:
                    await asyncio.sleep(.01)
            poller = asyncio.create_task(poll_worker_results())
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False), base_url='http://testserver') as client:
                    return await client.request(method, path, **kwargs)
            finally:
                poller.cancel()
                await asyncio.gather(poller, return_exceptions=True)
        return asyncio.run(perform())

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self.request('POST', path, **kwargs)

    def put(self, path, **kwargs):
        return self.request('PUT', path, **kwargs)


@pytest.fixture
def client(tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path, provider='openai', _env_file=None), provider)
    yield ASGIClient(app, provider)
