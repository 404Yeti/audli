"""Apply the explicit Postgres baseline without logging database credentials."""
from pathlib import Path
import psycopg
from app.config import Settings


def migrate(settings: Settings):
    if settings.persistence != 'postgres':
        raise RuntimeError('Set AUDLI_PERSISTENCE=postgres before running this migration')
    url = settings.database_url.get_secret_value().replace('postgresql+psycopg://', 'postgresql://', 1)
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as db:
            exists = db.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
            versions = db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall() if exists else []
            if versions not in ([], [(1,)], [(1,), (2,)]):
                raise ValueError('Unsupported schema version')
            changed = False
            root = Path(__file__).resolve().parents[1] / 'docs'
            for version, name in ((1, 'postgres.sql'), (2, 'postgres-002.sql')):
                if (version,) not in versions:
                    db.execute((root / name).read_text(), prepare=False)
                    changed = True
            return changed
    except Exception:
        raise RuntimeError('Postgres migration failed. Check backend database access and schema compatibility; credentials were not logged.') from None


if __name__ == '__main__':
    try:
        print('Postgres migrations applied.' if migrate(Settings()) else 'Postgres migrations already applied.')
    except Exception:
        raise SystemExit('Postgres migration could not run. Check persistence/auth configuration, AUDLI_DATABASE_URL and database/schema access.') from None
