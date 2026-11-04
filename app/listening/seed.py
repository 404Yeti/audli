"""Load the checked-in seed lesson into the catalog (idempotent). No provider call: audio is a local development fixture."""
import hashlib
import json
from pathlib import Path

from app.listening import store
from app.listening.domain import LessonContent, Cue, check_level_fit
from app.listening.objects import ObjectStore

FIXTURES = Path(__file__).parent / 'fixtures'
LESSONS = ('restaurant_b1', 'pharmacy_a2')     # every checked-in lesson, in library order


def load_fixture(name: str = 'restaurant_b1', journey: bool = True) -> tuple[LessonContent, store.AudioFile, bytes]:
    """journey=False yields the Milestone 01 shape (one multiple-choice question); the product always seeds the full journey."""
    folder = FIXTURES / name
    content = LessonContent.model_validate_json((folder / 'lesson.json').read_text())
    if not journey:
        content = content.model_copy(update={'spoken': None, 'fill_blank': None, 'fill_key': None, 'sequencing': None, 'sequence_key': None})
    problems = check_level_fit(content)
    if problems:
        raise ValueError('Script does not fit its level: ' + '; '.join(problems))
    meta = json.loads((folder / 'audio.json').read_text())
    data = (folder / meta['file']).read_bytes()
    if hashlib.sha256(data).hexdigest() != meta['sha256'] or len(data) != meta['size_bytes']:
        raise ValueError('Fixture audio does not match its recorded checksum; rebuild it with scripts/build_listening_fixture.py')
    key = f"lessons/{content.slug}/{meta['sha256'][:16]}.{'mp3' if meta['content_type'] == 'audio/mpeg' else 'wav'}"
    audio = store.AudioFile(accent=meta['accent'], voice=meta['voice'][:60], provider=meta['provider'], object_key=key,
                            content_type=meta['content_type'], sha256=meta['sha256'], size_bytes=meta['size_bytes'],
                            duration_ms=meta['duration_ms'], cues=[Cue(**c) for c in meta['cues']])
    return content, audio, data


def seed(engine, objects: ObjectStore, name: str = 'restaurant_b1', journey: bool = True) -> tuple[str, bool]:
    content, audio, data = load_fixture(name, journey)
    if not objects.exists(audio.object_key):
        objects.put(audio.object_key, data, audio.content_type)
        if not objects.exists(audio.object_key):      # never publish a version whose audio is not actually stored
            raise RuntimeError('Audio upload could not be confirmed; nothing was published.')
    with engine.begin() as db:
        return store.publish_version(db, content, audio)


def seed_all(engine, objects: ObjectStore) -> list[tuple[str, bool]]:
    """Publish every checked-in lesson (idempotent)."""
    return [seed(engine, objects, name) for name in LESSONS]
