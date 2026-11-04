"""Migration 009 SQL, generated from app/listening/schema.py exactly like 001-007 and checked in as docs/postgres-009.sql."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex

from app.listening import schema as l


def postgres_listening_migration() -> str:
    dialect = postgresql.dialect()
    names = ', '.join(l.NAMES)
    lines = [
        '\n'.join(['-- Migration 009: Audli 2.0 listening lessons (additive; no 1.x table is altered).',
        '-- Requires migrations 001 through 007 and leaves schema_migrations untouched: 1.x reads max(version) >= 8 as the claims',
        '-- schema, so 009 records itself in listening_migrations. Independent of 008. NEVER run at application startup.']),
        'BEGIN;', "SET LOCAL lock_timeout='5s';", 'LOCK TABLE public.schema_migrations IN EXCLUSIVE MODE;',
        """DO $$ BEGIN
 IF NOT (ARRAY[1,2,3,4,5,6,7] <@ ARRAY(SELECT version FROM public.schema_migrations)) THEN
  RAISE EXCEPTION 'Expected explicit migrations 001 through 007';
 END IF;
 IF to_regclass('public.listening_migrations') IS NOT NULL OR to_regclass('public.listening_lessons') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 009 is already applied or partially present; review before retrying';
 END IF;
END $$;""",
    ]
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip() + ';' for t in l.OWNED]
    for table in l.OWNED:
        lines += [str(CreateIndex(i).compile(dialect=dialect)) + ';' for i in sorted(table.indexes, key=lambda i: i.name)]
    lines += l.POSTGRES_TRIGGERS
    for table in l.OWNED:
        lines += [f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;', f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;']
    lines += [f"""DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE {names} FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE {names} FROM authenticated;
    END IF;
END
$audli$;""", 'INSERT INTO listening_migrations(version, applied_at) VALUES (9, now());', 'COMMIT;']
    return '\n'.join(line.rstrip() for line in '\n\n'.join(lines).splitlines()) + '\n'
