"""Versioned baseline SQL. Later changes must be new numbered migrations."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex
from app.storage.schema import metadata, lessons, lesson_exercises, accent_control, accent_reservations, roadmaps, HOMEWORK_TABLES, LIMIT_TABLES


def postgres_baseline():
    dialect = postgresql.dialect()
    tables = [table for table in metadata.sorted_tables if table not in (lessons, lesson_exercises, accent_control, accent_reservations, roadmaps, *HOMEWORK_TABLES, *LIMIT_TABLES)]
    statements = [
        '-- Migration 001: durable learner state. Run once as the backend database owner.',
        '-- Generated from app/storage/schema.py; append later numbered migrations, never edit an applied baseline.',
        '-- JSONB stores versioned domain snapshots; queryable fields and evidence have relational columns.',
        'BEGIN;',
    ]
    for table in tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ';')
    for table in tables:
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)) + ';')
    for table in tables:
        statements.append(f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;')
        statements.append(f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;')
    names = ', '.join(table.name for table in tables)
    # Supabase creates these roles; ordinary local Postgres does not require them.
    statements.append(f"""DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE {names} FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE {names} FROM authenticated;
    END IF;
END
$audli$;""")
    statements.extend(['INSERT INTO schema_migrations(version, applied_at) VALUES (1, now());', 'COMMIT;'])
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines()) + '\n'


def postgres_lesson_migration():
    dialect = postgresql.dialect()
    tables = [lessons, lesson_exercises]
    statements = ['-- Migration 002: structured conversational lessons.', 'BEGIN;']
    for table in tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ';')
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)) + ';')
        statements.append(f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;')
        statements.append(f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;')
    names = ', '.join(table.name for table in tables)
    statements.append(f"""DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE {names} FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE {names} FROM authenticated;
    END IF;
END
$audli$;""")
    statements.extend(['INSERT INTO schema_migrations(version, applied_at) VALUES (2, now());', 'COMMIT;'])
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines()) + '\n'


def postgres_accent_migration():
    dialect = postgresql.dialect()
    tables = [accent_control, accent_reservations, roadmaps]
    statements = ['-- Migration 003: fleet-wide accent admission controls; initially disabled.', 'BEGIN;']
    for table in tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ';')
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)) + ';')
        statements.extend([f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;',
                           f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;'])
    statements.append("""DO $audli$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE accent_control, accent_reservations, roadmaps FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE accent_control, accent_reservations, roadmaps FROM authenticated;
    END IF;
END
$audli$;""")
    statements.extend(["INSERT INTO accent_control VALUES (1,0,0,0,2,to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD'),0,now());",
                       'INSERT INTO schema_migrations(version, applied_at) VALUES (3, now());', 'COMMIT;'])
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines()) + '\n'


def postgres_planner_migration():
    dialect = postgresql.dialect()
    return '\n\n'.join([
        '-- Migration 004: owned adaptive roadmaps.', 'BEGIN;',
        str(CreateTable(roadmaps).compile(dialect=dialect)).strip() + ';',
        'ALTER TABLE learner_roadmaps ENABLE ROW LEVEL SECURITY;',
        'REVOKE ALL ON TABLE learner_roadmaps FROM PUBLIC;',
        "DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON learner_roadmaps FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON learner_roadmaps FROM authenticated; END IF; END $audli$;",
        'INSERT INTO schema_migrations(version, applied_at) VALUES (4, now());', 'COMMIT;',
    ]) + '\n'


def postgres_homework_migration():
    dialect = postgresql.dialect()
    statements = ['-- Migration 005: optional listening homework; backend-only access.', 'BEGIN;']
    for table in HOMEWORK_TABLES:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ';')
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)) + ';')
        statements.extend([f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;',
            f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;'])
    names = ', '.join(table.name for table in HOMEWORK_TABLES)
    statements.append(f"DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {names} FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {names} FROM authenticated; END IF; END $audli$;")
    statements.extend(['INSERT INTO schema_migrations(version, applied_at) VALUES (5, now());', 'COMMIT;'])
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines()) + '\n'


ENTITLEMENT_BACKSTOP = """-- Backstop for the application's atomic claim: no code path may start a new window early.
-- It compares only values on the row itself, so it does not depend on any clock.
CREATE FUNCTION lesson_entitlement_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
    IF NEW.last_started_at IS DISTINCT FROM OLD.last_started_at THEN
        IF NEW.last_started_at < OLD.next_eligible_at THEN
            RAISE EXCEPTION 'lesson_cooldown_active' USING ERRCODE = 'P0001';
        END IF;
        IF NEW.next_eligible_at <> NEW.last_started_at + interval '24 hours' THEN
            RAISE EXCEPTION 'lesson_window_invalid' USING ERRCODE = 'P0001';
        END IF;
    ELSIF NEW.next_eligible_at > OLD.next_eligible_at THEN
        RAISE EXCEPTION 'lesson_window_extension_refused' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END
$guard$;

CREATE TRIGGER lesson_entitlement_guard BEFORE UPDATE ON lesson_entitlements
    FOR EACH ROW EXECUTE FUNCTION lesson_entitlement_guard();"""


def postgres_lesson_limit_migration():
    dialect = postgresql.dialect()
    statements = ['-- Migration 006: durable daily lesson entitlement and the expired lesson status.', 'BEGIN;']
    for table in LIMIT_TABLES:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ';')
        statements.extend([f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;',
            f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;'])
    names = ', '.join(table.name for table in LIMIT_TABLES)
    statements.append(f"DO $audli$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {names} FROM anon; END IF; IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {names} FROM authenticated; END IF; END $audli$;")
    statements.append("""-- Existing learners keep their latest real lesson start; historical lesson rows are not altered.
INSERT INTO lesson_entitlements (user_id, last_started_at, next_eligible_at, last_lesson_id, updated_at)
SELECT DISTINCT ON (user_id) user_id, started_at, started_at + interval '24 hours', id, now()
FROM lesson_lifecycles ORDER BY user_id, started_at DESC, id DESC;""")
    statements.append(ENTITLEMENT_BACKSTOP)
    statements.append("""-- An expired lesson keeps its partial work but is neither open nor completed.
ALTER TABLE lesson_lifecycles DROP CONSTRAINT lesson_status_valid;
ALTER TABLE lesson_lifecycles ADD CONSTRAINT lesson_status_valid CHECK (status IN ('active','paused','completed','expired'));
DROP INDEX one_open_lesson_per_learner;
CREATE UNIQUE INDEX one_open_lesson_per_learner ON lesson_lifecycles (user_id) WHERE status IN ('active','paused');""")
    statements.extend(['INSERT INTO schema_migrations(version, applied_at) VALUES (6, now());', 'COMMIT;'])
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines()) + '\n'
