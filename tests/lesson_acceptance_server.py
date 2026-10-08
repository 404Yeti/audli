"""Isolated acceptance server: real routes, SQLite and espeak, prescribed AI evidence.

Only launched explicitly by lesson-media.playwright.config.ts. No production fixture
endpoints, live learner data, microphone, provider keys, or model accuracy claims.
"""
import os
import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path

_workspace = tempfile.TemporaryDirectory(prefix='aud22-native-media-')
os.environ.update(AUDLI_ENVIRONMENT='development', AUDLI_PERSISTENCE='sqlite',
    AUDLI_DATABASE_URL='', AUDLI_LEARNER_ID='', AUDLI_AUTH_MODE='local',
    AUDLI_DATA_DIR=_workspace.name, AUDLI_SUPABASE_URL='', AUDLI_SUPABASE_PUBLISHABLE_KEY='')

from app.config import Settings
from app.main import create_app
from app.repository import ProgressRepository
from app.services.demo import DemoProvider
from app.models import Transcription, EvidenceAssessment
from app.evaluation import expected_units
import app.lesson_api as lesson_api
from app.lesson import now

class AcceptanceProvider(DemoProvider):
    generated = 0
    recognized = 0
    assessed = 0

    async def speak(self, text, speech_rate=.9):
        # A host may supply a standalone espeak binary/data directory. Real TTS
        # bytes are still decoded, sanitized and played through application routes.
        command = [os.environ.get('AUDLI_TEST_ESPEAK_BIN', 'espeak'), '--stdout',
                   '-s', str(round(150*speech_rate)), text]
        if path := os.environ.get('AUDLI_TEST_ESPEAK_DATA'):
            command.insert(1, '--path=' + path)
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
        audio, error = await process.communicate()
        if process.returncode:
            raise RuntimeError('Acceptance speech fixture failed: ' + error.decode()[:200])
        return audio

    async def generate(self, profile):
        self.generated += 1
        content = await super().generate(profile)
        pottery = self.generated == 1
        return content.model_copy(update={
            'title': 'An afternoon at the workshop' if pottery else 'Bread for the neighbors',
            'topic': 'Pottery making' if pottery else 'Baking bread',
            'script': ('Maya went to a pottery workshop. She wanted to make a small bowl for her kitchen. '
                'First, she softened the clay with water. Then she shaped it slowly on the wheel. '
                'The teacher helped her make the sides even. Maya chose a blue finish. The bowl needed '
                'to dry before it could go into the hot kiln. The workshop would fire it on Friday. '
                'Maya felt patient because making a useful bowl takes several careful steps.') if pottery else (
                'Maya baked bread for her neighbors. She mixed flour, water, salt, and yeast in a large bowl. '
                'Then she kneaded the dough until it felt smooth. She let it rise in a warm place. '
                'After an hour, she shaped two loaves. The bread baked until the top became golden. '
                'Maya waited for it to cool before slicing it. She shared one loaf with a neighbor. '
                'The ingredients cost less when she bought a larger bag of flour.'),
            'main_idea': 'Maya learned the steps of making a bowl' if pottery else 'Maya made bread to share with neighbors',
            'important_details': ['The workshop would fire the bowl on Friday', 'Maya chose a blue finish', 'The clay needed to dry'] if pottery else
                ['The larger bag of flour reduced the cost', 'She shaped two loaves', 'The dough rose in a warm place'],
            'inference_points': ['Making a bowl requires patience'] if pottery else ['Maya enjoys sharing food'],
            'vocabulary_items': [{'phrase':'shaped','meaning_in_context':'formed into the desired shape'},
                {'phrase':'careful' if pottery else 'golden','meaning_in_context':'done with attention' if pottery else 'brown from baking'}],
            'questions': [{'question':'What was Maya doing?', 'expected_information':['making a bowl' if pottery else 'baking bread']}],
        })

    async def transcribe(self, audio, filename):
        self.recognized += 1
        return Transcription(text="I'm good, and you?" if self.recognized == 1 else
            'Maya made a bowl. I thought the firing was on Monday.' if self.recognized == 2 else
            'Maya baked bread to share. I thought the larger bag cost more.', confidence=.99)

    async def assess(self, exercise, turns):
        self.assessed += 1
        if self.assessed == 2:
            # Controlled authoritative clock: finish the active exercise, then close.
            lesson_api.now = lambda: now() + timedelta(seconds=550)
        return EvidenceAssessment(units=[{'dimension':dimension,'index':index,
            'status':'misunderstood' if dimension == 'details' and index == 0 else 'demonstrated',
            'evidence':turns[-1].text,'question':''}
            for dimension, units in expected_units(exercise).items() for index in range(len(units))],
            feedback='Synthetic acceptance assessment, not real model accuracy.',
            transcription_concern=False, concern_reason='')

repository = ProgressRepository(Path(_workspace.name)/'audli.sqlite3')
profile = repository.profile(); profile.name='Maya'; profile.onboarding_status='complete'; repository.save_profile(profile)
app = create_app(Settings(provider='demo', data_dir=Path(_workspace.name), _env_file=None), AcceptanceProvider(), repository)
