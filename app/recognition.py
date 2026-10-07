"""Conservative recognition gate for automatic, unedited spoken answers."""
from app.models import Transcription


def reliable_recognition(transcription: Transcription, minimum: float) -> bool:
    return bool(transcription.text.strip() and not transcription.uncertainty
                and transcription.confidence is not None
                and transcription.confidence >= minimum)
