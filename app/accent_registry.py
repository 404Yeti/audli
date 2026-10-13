"""Accent vocabulary and voice lifecycle. Candidate metadata never grants routing."""
from typing import Literal
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

Accent = Literal['british', 'american', 'australian', 'scottish', 'indian', 'irish',
    'canadian', 'new_zealand', 'welsh', 'south_african', 'chinese', 'japanese',
    'filipino', 'nigerian', 'southern', 'new_york', 'boston']

LABELS = dict(zip(Accent.__args__, ['British English', 'American English',
    'Australian English', 'Scottish English', 'Indian English', 'Irish English',
    'Canadian English', 'New Zealand English', 'Welsh English', 'South African English',
    'Chinese English', 'Japanese English', 'Filipino English', 'Nigerian English',
    'Southern US English', 'New York English', 'Boston English']))
ALIASES = {'british': r'british|britain|england|uk',
    'american': r'american|america|usa|us accent', 'australian': r'australian|australia|aussie',
    'scottish': r'scottish|scotland|scots', 'indian': r'indian|india', 'irish': r'irish|ireland',
    'canadian': r'canadian|canada', 'new_zealand': r'new zealand|kiwi',
    'welsh': r'welsh|wales', 'south_african': r'south african|south africa',
    'chinese': r'chinese', 'japanese': r'japanese', 'filipino': r'filipino|philippine',
    'nigerian': r'nigerian', 'southern': r'southern(?: (?:us|u\.s\.|american|united states))?|(?:us|american) southern',
    'new_york': r'new york(?:er)?', 'boston': r'boston(?:ian)?'}

class VoiceRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    accent: Accent
    voice_id: str = Field(pattern=r'^[A-Za-z0-9]{20}$')
    status: Literal['candidate', 'approved', 'enabled', 'unavailable']
    name: str
    verification_status: str
    wave: Literal[1, 2, 3] | None = None

# Inventory only. Deployment configuration and hash-validated human review
# remain authoritative; importing this module enables nothing.
EXISTING_VOICES = (
    VoiceRecord(accent='british', voice_id='onwK4e9ZLuTAKqWW03F9', status='enabled', name='Existing British voice', verification_status='existing production configuration'),
    VoiceRecord(accent='australian', voice_id='tyepWYJJwJM9TTFIg5U7', status='enabled', name='Existing Australian voice', verification_status='AUD-35 end-to-end verified; owner reports too quiet; replacement pending'),
    VoiceRecord(accent='irish', voice_id='DbwWo4rVEd5NrejHYUnm', status='enabled', name='Existing Irish voice', verification_status='existing production configuration'),
)

SELECTED_NAMES = {
    'indian': 'Pooja - Upbeat UGC Creator',
    'scottish': 'Chris - Scottish Casual',
    'canadian': 'Catherine - Educational & Conversational',
    'new_zealand': 'Torey Slatter',
    'chinese': 'Ziyu - Friendly but Unclear',
    'japanese': 'Yuki - Gentle and Precise',
    'filipino': 'Amara - English FIlipino Calm Narrator',
    'nigerian': 'Ola - Nigerian Storyteller',
    'southern': 'Wade - Southern Backwoods Neighbor',
    'new_york': 'Jerry B. - New York Italian Mobster',
    'boston': 'Jerry B. - Boston Accent Conversational Voice',
}

# Owner listening approval and production release authorization recorded in
# docs/aud38-release-evidence. Runtime still requires the explicit deployment
# flag, protected provider credentials, evidence hashes and fleet admission.
SELECTED_VOICES = {
    accent: VoiceRecord(accent=accent, voice_id=voice_id, status='enabled',
        name=SELECTED_NAMES[accent], wave=wave,
        verification_status='AUD-38 account access and Flash2.5 sample verified; owner listening and production release approved')
    for accent, voice_id, wave in (
        ('indian', 'fydPJq00SigRIaPxWgiS', 1),
        ('scottish', 'csXxiUN2BUFflsCaDxPM', 1),
        ('canadian', 'RorawgRgSS2YxWqy31vY', 1),
        ('new_zealand', 'pDZ0CqONaFi2LrK1f413', 1),
        ('chinese', '1a0nAYA3FcNQcMMfbddY', 2),
        ('japanese', 'DiHqGqDdN8tWkUxlsPeg', 2),
        ('filipino', '7IA6NDxeqmtXCKLKb5nf', 2),
        ('nigerian', '8P18CIVcRlwP98FOjZDm', 2),
        ('southern', '9XNfIsDIvdnSURk8DW4z', 3),
        ('new_york', 'QzTKubutNn9TjrB7Xb2Q', 3),
        ('boston', 'UZvBfqEdvCFLqsBOo9Zr', 3),
    )
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
