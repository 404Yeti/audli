"""Load the checked-in seed lesson into the catalog (idempotent). No provider call: audio is a local development fixture."""
import hashlib
import json
from pathlib import Path

from app.listening import store
from app.listening.domain import LessonContent, Cue, check_level_fit
from app.listening.objects import ObjectStore

FIXTURES = Path(__file__).parent / 'fixtures'
LESSONS = ('restaurant_b1', 'pharmacy_a2')     # every checked-in lesson, in library order


def load_fixture(name: str = 'restaurant_b1', journey: bool = True, audio_dir: Path | None = None,
                 revision: int | None = None) -> tuple[LessonContent, store.AudioFile, bytes]:
    """journey=False yields the Milestone 01 shape (one multiple-choice question); the product always seeds the full journey."""
    folder = FIXTURES / name
    content = LessonContent.model_validate_json((folder / 'lesson.json').read_text())
    if not journey:
        content = content.model_copy(update={'spoken': None, 'fill_blank': None, 'fill_key': None, 'sequencing': None, 'sequence_key': None})
    problems = check_level_fit(content)
    if problems:
        raise ValueError('Script does not fit its level: ' + '; '.join(problems))
    if revision is not None:
        content = LessonContent.model_validate({**content.model_dump(), 'revision': revision})      # same script, new recording: a new immutable version (validated: >= 2)
    source = Path(audio_dir) if audio_dir else folder                          # audio built by scripts/build_listening_audio.py, or the checked-in fixture
    meta = json.loads((source / 'audio.json').read_text())
    data = (source / meta['file']).read_bytes()
    if hashlib.sha256(data).hexdigest() != meta['sha256'] or len(data) != meta['size_bytes']:
        raise ValueError('Audio does not match its recorded checksum; rebuild it (scripts/build_listening_audio.py or scripts/build_listening_fixture.py)')
    key = f"lessons/{content.slug}/{meta['sha256'][:16]}.{'mp3' if meta['content_type'] == 'audio/mpeg' else 'wav'}"
    audio = store.AudioFile(accent=meta['accent'], voice=meta['voice'][:60], provider=meta['provider'], object_key=key,
                            content_type=meta['content_type'], sha256=meta['sha256'], size_bytes=meta['size_bytes'],
                            duration_ms=meta['duration_ms'], cues=[Cue(**c) for c in meta['cues']])
    return content, audio, data


def seed(engine, objects: ObjectStore, name: str = 'restaurant_b1', journey: bool = True, audio_dir: Path | None = None, revision: int | None = None,
         production: bool = False) -> tuple[str, bool]:
    content, audio, data = load_fixture(name, journey, audio_dir, revision)
    if production and audio.provider in ('fixture', 'fake'):
        raise ValueError('Refusing to publish development (fixture or fake) audio in production: build natural audio with scripts/build_listening_audio.py first.')
    if not objects.exists(audio.object_key):
        objects.put(audio.object_key, data, audio.content_type)
        if not objects.exists(audio.object_key):      # never publish a version whose audio is not actually stored
            raise RuntimeError('Audio upload could not be confirmed; nothing was published.')
    with engine.begin() as db:
        return store.publish_version(db, content, audio)


def seed_all(engine, objects: ObjectStore, audio_root: Path | None = None, revision: int | None = None, production: bool = False) -> list[tuple[str, bool]]:
    """Publish every checked-in lesson (idempotent). With audio_root, each lesson takes its audio from audio_root/<lesson>/ instead of the fixture."""
    return [seed(engine, objects, name, audio_dir=(Path(audio_root) / name if audio_root else None), revision=revision, production=production) for name in LESSONS]
