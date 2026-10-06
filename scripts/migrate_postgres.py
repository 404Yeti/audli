"""Apply the explicit Postgres baseline without logging database credentials."""
from pathlib import Path
import psycopg
from app.config import Settings
from app.storage.adapters import SCHEMA_VERSION


def migrate(settings: Settings):
    if settings.persistence != 'postgres':
        raise RuntimeError('Set AUDLI_PERSISTENCE=postgres before running this migration')
    url = settings.database_url.get_secret_value().replace('postgresql+psycopg://', 'postgresql://', 1)
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as db:
            exists = db.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
            if exists:
                versions = db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()
                if versions != [(SCHEMA_VERSION,)]:
                    raise ValueError('Unsupported schema version')
                return False
            # No IF NOT EXISTS: unrelated/incompatible tables must not be silently adopted.
            sql = (Path(__file__).resolve().parents[1] / 'docs' / 'postgres.sql').read_text()
            db.execute(sql, prepare=False)
            return True
    except Exception:
        raise RuntimeError('Postgres migration failed. Check backend database access and schema compatibility; credentials were not logged.') from None


if __name__ == '__main__':
    try:
        print('Postgres migration 001 applied.' if migrate(Settings()) else 'Postgres migration 001 already applied.')
    except Exception:
        raise SystemExit('Postgres migration could not run. Check persistence/auth configuration, AUDLI_DATABASE_URL and database/schema access.') from None
