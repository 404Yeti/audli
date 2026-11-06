"""Accent vocabulary and voice lifecycle. Candidate metadata never grants routing."""
from typing import Literal
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from app.accent_catalog import (ACCENT_IDS, ALIASES, LABELS, EXISTING_PASSAGE_VOICES, SELECTED_PASSAGE_VOICES, SELECTED_VERIFICATION)       # shared, data-only source of truth (also read by Audli 2.0)

Accent = Literal[ACCENT_IDS]    # type: ignore[valid-type]  # the vocabulary lives in app/accent_catalog.py

class VoiceRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    accent: Accent
    voice_id: str = Field(pattern=r'^[A-Za-z0-9]{20}$')
    status: Literal['candidate', 'approved', 'enabled', 'unavailable']
    name: str
    verification_status: str
    wave: Literal[1, 2, 3] | None = None

# Inventory only. Deployment configuration and hash-validated human review
# remain authoritative; importing this module enables nothing. The data lives in app/accent_catalog.py.
EXISTING_VOICES = tuple(VoiceRecord(accent=accent, voice_id=voice_id, status=status, name=name, verification_status=verification)
                        for accent, voice_id, status, name, verification in EXISTING_PASSAGE_VOICES)

SELECTED_NAMES = {accent: name for accent, _, _, name in SELECTED_PASSAGE_VOICES}

# Owner listening approval and production release authorization recorded in
# docs/aud38-release-evidence. Runtime still requires the explicit deployment
# flag, protected provider credentials, evidence hashes and fleet admission.
SELECTED_VOICES = {
    accent: VoiceRecord(accent=accent, voice_id=voice_id, status='enabled',
        name=name, wave=wave, verification_status=SELECTED_VERIFICATION)
    for accent, voice_id, wave, name in SELECTED_PASSAGE_VOICES
}

def routing_allowed(accent: str, voice_id: str) -> bool:
    selected = SELECTED_VOICES.get(accent)
    return selected is None or (selected.status == 'enabled' and selected.voice_id == voice_id)

def candidate_inventory(path: Path) -> tuple[VoiceRecord, ...]:
    """Load metadata-verified IDs for tooling; never used as runtime settings."""
    data = json.loads(path.read_text())
    return tuple(VoiceRecord(accent=accent, voice_id=row['voice_id'],
        status=row['status'], name=row['name'], verification_status=row['verification_status'])
        for accent, group in data['accents'].items() for row in group['voices'])
