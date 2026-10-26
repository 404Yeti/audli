"""Local-only AUD-91 browser harness. Never imported by application startup.

Real API/SQLite/audio validation and real STT classification; prescribed SDK
transport and synthetic WAV output. No remote provider or production credentials.
"""
import io
import math
import os
import struct
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

_workspace = tempfile.TemporaryDirectory(prefix='aud91-short-choice-')
os.environ.update(AUDLI_ENVIRONMENT='development', AUDLI_PERSISTENCE='sqlite',
    AUDLI_DATABASE_URL='', AUDLI_LEARNER_ID='', AUDLI_AUTH_MODE='local',
    AUDLI_DATA_DIR=_workspace.name, AUDLI_SUPABASE_URL='', AUDLI_SUPABASE_PUBLISHABLE_KEY='')

from fastapi import FastAPI
from pydantic import Field
from app.config import Settings
from app.main import create_app
from app.models import AccentPreferences, StrictModel
from app.repository import ProgressRepository
from app.services.demo import DemoProvider
from app.services.openai_provider import OpenAIProvider


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
        audio.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000)))
                                   for i in range(24000)))
    return output.getvalue()


class Choice(StrictModel):
    text: str = Field(default='Yes, please.', max_length=100)
    confidence: float = Field(default=.95, ge=0, le=1)
    offered: bool = True


class ChoiceProvider(DemoProvider):
    # Keep the actual provider's confidence/short-answer classification.
    transcribe = OpenAIProvider.transcribe

    def __init__(self, choice, settings):
        self.settings, self.calls = settings, 0
        async def create(**kwargs):
            assert kwargs['file'][1] and kwargs['include'] == ['logprobs']
            self.calls += 1
            return SimpleNamespace(text=choice.text,
                logprobs=[SimpleNamespace(logprob=math.log(max(choice.confidence, 1e-10)))])
        self.client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    async def speak(self, text, speech_rate=.9):
        return wav()


app = FastAPI()
current = None
generation = 0


@app.post('/__fixture/reset')
async def reset(choice: Choice):
    global current, generation
    if current is not None:
        current.state.repository.close()
        current.state.accent_controls.close()
    generation += 1
    directory = Path(_workspace.name) / str(generation)
    directory.mkdir()
    repo = ProgressRepository(directory / 'test.sqlite3')
    profile = repo.profile()
    profile.name = 'Listener'; profile.onboarding_status = 'complete'
    profile.accent_preferences = AccentPreferences(status='preferred' if choice.offered else 'no_preference',
                                                  accents=['australian'] if choice.offered else [])
    repo.save_profile(profile)
    settings = Settings(provider='demo', data_dir=directory, _env_file=None)
    provider = ChoiceProvider(choice, settings)
    current = create_app(settings, provider, repo)
    async def voices():
        return {'australian': object()}  # Only the offer/intent paths run; no passage provider.
    current.state.accent_controls.voices = voices
    mounted.app = current
    app.state.provider = provider
    return {'ready': True}


@app.get('/__fixture/metrics')
async def metrics():
    lesson = current.state.repository.lesson()
    return {'transcriptions': app.state.provider.calls, 'phase': lesson.phase if lesson else None,
            'consent': lesson.accent_consent.state if lesson else None}


# The fixture-control routes above never exist in the production application.
app.mount('/', FastAPI())
mounted = app.routes[-1]
