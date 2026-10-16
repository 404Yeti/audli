"""Versioned baseline SQL. Later changes must be new numbered migrations."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex
from app.storage.schema import metadata, lessons, lesson_exercises, accent_control, accent_reservations, roadmaps, HOMEWORK_TABLES


def postgres_baseline():
    dialect = postgresql.dialect()
    tables = [table for table in metadata.sorted_tables if table not in (lessons, lesson_exercises, accent_control, accent_reservations, roadmaps, *HOMEWORK_TABLES)]
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
    return '\n\n'.join(statements) + '\n'
