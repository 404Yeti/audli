"""Publish the seed listening lesson into the configured database and object store (idempotent, no paid provider).
    AUDLI_LISTENING_ENABLED=true .venv/bin/python -m scripts.seed_listening_lesson
Production needs migration 009 and S3 storage configured; this script never runs migrations.
"""
from app.config import Settings
from app.listening.api import prepare
from app.listening.objects import build_store
from app.listening.seed import seed_all
from app.storage.adapters import create_repository


def main() -> None:
    settings = Settings()
    if not settings.listening_enabled:
        raise SystemExit('Set AUDLI_LISTENING_ENABLED=true first.')
    database = create_repository(settings)
    try:
        prepare(database.engine)
        for version_id, created in seed_all(database.engine, build_store(settings, settings.data_dir)):
            print(('Published' if created else 'Already published') + f' lesson version {version_id}')
    finally:
        database.close()


if __name__ == '__main__':
    main()
