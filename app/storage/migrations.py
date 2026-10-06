"""Versioned baseline SQL. Later changes must be new numbered migrations."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex
from app.storage.schema import metadata


def postgres_baseline():
    dialect = postgresql.dialect()
    statements = [
        '-- Migration 001: durable learner state. Run once as the backend database owner.',
        '-- Generated from app/storage/schema.py; append later numbered migrations, never edit an applied baseline.',
        '-- JSONB stores versioned domain snapshots; queryable fields and evidence have relational columns.',
        'BEGIN;',
    ]
    for table in metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ';')
    for table in metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)) + ';')
    for table in metadata.sorted_tables:
        statements.append(f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;')
        statements.append(f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;')
    names = ', '.join(table.name for table in metadata.sorted_tables)
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
