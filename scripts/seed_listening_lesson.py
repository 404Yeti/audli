"""Publish the checked-in lessons into the configured database and object store (idempotent, no paid provider).
    AUDLI_LISTENING_ENABLED=true .venv/bin/python -m scripts.seed_listening_lesson
    AUDLI_LISTENING_ENABLED=true .venv/bin/python -m scripts.seed_listening_lesson --audio-root build/listening-audio --revision 2   # natural audio, as a new version
Production needs migrations 009-011 and S3 storage configured; this script never runs migrations. In production it refuses development (espeak) audio.
"""
import argparse
from pathlib import Path

from app.config import Settings
from app.listening.api import prepare
from app.listening.objects import build_store
from app.listening.seed import seed_all
from app.storage.adapters import create_repository


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio-root', type=Path, help='folder of <lesson>/audio.mp3 + audio.json built by scripts.build_listening_audio')
    parser.add_argument('--revision', type=int, help='>= 2: publish this audio as a new version of the same script')
    args = parser.parse_args(argv)
    if bool(args.audio_root) != bool(args.revision):
        raise SystemExit('--audio-root and --revision go together: new audio is always a new version.')
    settings = Settings()
    if not settings.listening_enabled:
        raise SystemExit('Set AUDLI_LISTENING_ENABLED=true first.')
    database = create_repository(settings)
    try:
        prepare(database.engine)
        for version_id, created in seed_all(database.engine, build_store(settings, settings.data_dir), args.audio_root, args.revision,
                                            production=settings.environment == 'production'):
            print(('Published' if created else 'Already published') + f' lesson version {version_id}')
    finally:
        database.close()


if __name__ == '__main__':
    main()
