"""Migration 009 and 010 SQL, generated from app/listening/schema.py exactly like 001-007 and checked in as docs/postgres-009.sql and
docs/postgres-010.sql. 009 is frozen: it must keep generating byte-identical SQL."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex

from app.listening import schema as l


# 009 shipped with this narrower kind check; 010 replaces it. Keeping the 009 text exact is what a repeatable generator needs.
_KIND_009 = ("CHECK (kind IN ('multiple_choice','spoken','fill_blank','sequencing') AND position >= 1)", "CHECK (kind = 'multiple_choice' AND position >= 1)")
_FIRST = [t for t in l.OWNED if not t.info.get('since')]
_SECOND = [t for t in l.OWNED if t.info.get('since') == 10]
_THIRD = [t for t in l.OWNED if t.info.get('since') == 11]


def postgres_listening_migration() -> str:
    dialect = postgresql.dialect()
    names = ', '.join(t.name for t in _FIRST)
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
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip().replace(*_KIND_009) + ';' for t in _FIRST]
    for table in _FIRST:
        lines += [str(CreateIndex(i).compile(dialect=dialect)) + ';' for i in sorted(table.indexes, key=lambda i: i.name)]
    lines += l.POSTGRES_TRIGGERS
    for table in _FIRST:
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


def postgres_listening_migration_010() -> str:
    """Milestone 02: fill-in-the-blank, sequencing, spoken response and the job queue. Additive except for widening one CHECK."""
    dialect = postgresql.dialect()
    names = ', '.join(t.name for t in _SECOND)
    lines = [
        '\n'.join(['-- Migration 010: Audli 2.0 four-exercise journey (rubrics, answers, spoken responses, durable jobs).',
        '-- Requires 009 (recorded in listening_migrations). Widens listening_exercises.kind; no 1.x table is touched and',
        '-- schema_migrations is untouched. NEVER run at application startup.']),
        'BEGIN;', "SET LOCAL lock_timeout='5s';",
        'LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;',
        """DO $$ BEGIN
 IF NOT (ARRAY[9] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migration 009 first';
 END IF;
 IF 10 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_jobs') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 010 is already applied or partially present; review before retrying';
 END IF;
END $$;""",
        'ALTER TABLE listening_exercises DROP CONSTRAINT listening_exercise_kind;',
        "ALTER TABLE listening_exercises ADD CONSTRAINT listening_exercise_kind "
        "CHECK (kind IN ('multiple_choice','spoken','fill_blank','sequencing') AND position >= 1);",
    ]
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip() + ';' for t in _SECOND]
    for table in _SECOND:
        lines += [str(CreateIndex(i).compile(dialect=dialect)) + ';' for i in sorted(table.indexes, key=lambda i: i.name)]
    lines += l.POSTGRES_TRIGGERS_010
    for table in _SECOND:
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
$audli$;""", 'INSERT INTO listening_migrations(version, applied_at) VALUES (10, now());', 'COMMIT;']
    return '\n'.join(line.rstrip() for line in '\n\n'.join(lines).splitlines()) + '\n'


def postgres_listening_migration_011() -> str:
    """Global AI spending ceiling and kill switch: one additive table. NEVER run at application startup."""
    dialect = postgresql.dialect()
    names = ', '.join(t.name for t in _THIRD)
    lines = [
        '\n'.join(['-- Migration 011: Audli 2.0 AI spending ceiling and kill switch (one additive table).',
        '-- Requires 010 (recorded in listening_migrations). Leaves schema_migrations untouched.']),
        'BEGIN;', "SET LOCAL lock_timeout='5s';", 'LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;',
        """DO $$ BEGIN
 IF NOT (ARRAY[9, 10] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009 and 010 first';
 END IF;
 IF 11 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_ai_limits') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 011 is already applied or partially present; review before retrying';
 END IF;
END $$;""",
    ]
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip() + ';' for t in _THIRD]
    for table in _THIRD:
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
$audli$;""", 'INSERT INTO listening_migrations(version, applied_at) VALUES (11, now());', 'COMMIT;']
    return '\n'.join(line.rstrip() for line in '\n\n'.join(lines).splitlines()) + '\n'
