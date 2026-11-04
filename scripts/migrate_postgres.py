"""Apply the explicit Postgres baseline without logging database credentials."""
from pathlib import Path
import psycopg
from app.config import Settings


# (version, file) applied in order, recorded in listening_migrations.
LISTENING_MIGRATIONS = ((9, 'postgres-009.sql'),)


def migrate(settings: Settings, listening: bool = False):
    if settings.persistence != 'postgres':
        raise RuntimeError('Set AUDLI_PERSISTENCE=postgres before running this migration')
    url = settings.database_url.get_secret_value().replace('postgresql+psycopg://', 'postgresql://', 1)
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as db:
            exists = db.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
            versions = db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall() if exists else []
            if versions not in ([], [(1,)], [(1,), (2,)], [(1,), (2,), (3,)], [(1,), (2,), (3,), (4,)], [(1,), (2,), (3,), (4,), (5,)], [(1,), (2,), (3,), (4,), (5,), (6,)], [(1,), (2,), (3,), (4,), (5,), (6,), (7,)], [(i,) for i in range(1,9)]):
                raise ValueError('Unsupported schema version')
            changed = False
            root = Path(__file__).resolve().parents[1] / 'docs'
            for version, name in ((1, 'postgres.sql'), (2, 'postgres-002.sql'), (3, 'postgres-003.sql'), (4, 'postgres-004.sql'), (5, 'postgres-005.sql'), (6, 'postgres-006.sql'), (7, 'postgres-007.sql')):
                if (version,) not in versions:
                    db.execute((root / name).read_text(), prepare=False)
                    changed = True
            if listening:
                # Explicit opt-in (python -m scripts.migrate_postgres --listening). The 2.0 module keeps its own ledger on purpose:
                # app/claims/runtime.py reads max(schema_migrations.version) >= 8 as "claims schema present" and migration 008
                # asserts that ledger is exactly 1-7, so a 9 there would stop 1.x from starting. 009 needs only 001-007 (checked by
                # its SQL) and works before or after 008. Future 2.0 migrations are appended to LISTENING_MIGRATIONS.
                applied = set()
                if db.execute("SELECT to_regclass('public.listening_migrations')").fetchone()[0] is not None:
                    applied = {row[0] for row in db.execute('SELECT version FROM listening_migrations')}
                for version, name in LISTENING_MIGRATIONS:
                    if version not in applied:
                        db.execute((root / name).read_text(), prepare=False)
                        changed = True
            return changed
    except Exception:
        raise RuntimeError('Postgres migration failed. Check backend database access and schema compatibility; credentials were not logged.') from None


if __name__ == '__main__':
    try:
        print('Postgres migrations applied.' if migrate(Settings(), listening='--listening' in __import__('sys').argv) else 'Postgres migrations already applied.')
    except Exception:
        raise SystemExit('Postgres migration could not run. Check persistence/auth configuration, AUDLI_DATABASE_URL and database/schema access.') from None
