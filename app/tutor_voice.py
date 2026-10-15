"""Account preference and immutable tutor-only voice selection."""
from typing import Literal
from app.models import StrictModel
from app.config import Settings

TutorVoice = Literal['original', 'male']
VOICE_IDS = {'original': 'yKYzqEa22xh5PdidhN70', 'male': 'OrG4D9yQsedssYoRLflr'}
VOICE_LABELS = {'original': 'Audli — Original', 'male': 'Audli — Male'}


class TutorVoiceUpdate(StrictModel):
    voice: TutorVoice


def tutor_settings(settings: Settings, voice: TutorVoice) -> Settings:
    # Original retains the deployment's existing default and cache identity.
    # Never mutate the shared provider/settings or any passage configuration.
    selected = settings.elevenlabs_voice_id if voice == 'original' else VOICE_IDS[voice]
    return settings.model_copy(update={'elevenlabs_voice_id': selected})
