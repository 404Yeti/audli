"""Conservative recognition gate for automatic, unedited spoken answers."""
from app.models import Transcription

SHORT_ASSESSMENT_WARNING = 'Too little recognized speech to evaluate reliably.'


def reliable_recognition(transcription: Transcription, minimum: float) -> bool:
    return bool(transcription.text.strip() and not transcription.uncertainty
                and transcription.confidence is not None
                and transcription.confidence >= minimum)
