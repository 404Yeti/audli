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
_FOURTH = [t for t in l.OWNED if t.info.get('since') == 12]
_FIFTH = [t for t in l.OWNED if t.info.get('since') == 14]
_SIXTH = [t for t in l.OWNED if t.info.get('since') == 15]


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


def postgres_listening_migration_012() -> str:
    """AI lesson generation: private lesson ownership, generation requests and a separate generation spending ledger (three additive tables)."""
    dialect = postgresql.dialect()
    names = ', '.join(t.name for t in _FOURTH)
    lines = [
        '\n'.join(['-- Migration 012: Audli 2.0 AI lesson generation (three additive tables: lesson owners, generation requests, generation limits).',
        '-- Requires 011 (recorded in listening_migrations). Leaves schema_migrations untouched and alters no existing table.']),
        'BEGIN;', "SET LOCAL lock_timeout='5s';", 'LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;',
        """DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010 and 011 first';
 END IF;
 IF 12 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_generation_requests') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 012 is already applied or partially present; review before retrying';
 END IF;
END $$;""",
    ]
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip() + ';' for t in _FOURTH]
    for table in _FOURTH:
        lines += [str(CreateIndex(i).compile(dialect=dialect)) + ';' for i in sorted(table.indexes, key=lambda i: i.name)]
    for table in _FOURTH:
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
$audli$;""", 'INSERT INTO listening_migrations(version, applied_at) VALUES (12, now());', 'COMMIT;']
    return '\n'.join(line.rstrip() for line in '\n\n'.join(lines).splitlines()) + '\n'


def postgres_listening_migration_014() -> str:
    """Curated shared library: one workflow table. Alters nothing; no learner read path depends on it. Independent of 013 (account erasure)."""
    dialect = postgresql.dialect()
    names = ', '.join(t.name for t in _FIFTH)
    lines = [
        '\n'.join(['-- Migration 014: Audli 2.0 curated library (one additive table: listening_library_entries).',
        '-- Requires 009-012 (recorded in listening_migrations). Leaves schema_migrations untouched and alters no existing table. Independent of 013.']),
        'BEGIN;', "SET LOCAL lock_timeout='5s';", 'LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;',
        """DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11, 12] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010, 011 and 012 first';
 END IF;
 IF 14 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_library_entries') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 014 is already applied or partially present; review before retrying';
 END IF;
END $$;""",
    ]
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip() + ';' for t in _FIFTH]
    for table in _FIFTH:
        lines += [str(CreateIndex(i).compile(dialect=dialect)) + ';' for i in sorted(table.indexes, key=lambda i: i.name)]
    for table in _FIFTH:
        lines += [f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;', f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;']
    # Fail closed, in this one transaction: Supabase default privileges hand a new public table to anon, authenticated and service_role (including TRUNCATE,
    # which row-level security does not cover). Revoke ALL (every privilege, TRUNCATE included) from each of them, give the API role SELECT only, then
    # ASSERT the resulting privilege set. Any unexpected grantee aborts and rolls back the whole migration, so no state with stray privileges can be committed.
    lines += [f"""DO $audli$
DECLARE client text;
BEGIN
    FOREACH client IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = client) THEN
            EXECUTE 'REVOKE ALL ON TABLE {names} FROM ' || quote_ident(client);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audli_api_runtime') THEN
        GRANT SELECT ON TABLE {names} TO audli_api_runtime;
        CREATE POLICY audli_api_runtime_listening_read ON {names} FOR SELECT TO audli_api_runtime USING (true);
    END IF;
END
$audli$;""", f"""DO $audli$
DECLARE bad text;
BEGIN
    SELECT string_agg(coalesce(r.rolname, 'PUBLIC') || ':' || a.privilege_type, ', ') INTO bad
      FROM pg_class c
      CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
      LEFT JOIN pg_roles r ON r.oid = a.grantee
     WHERE c.oid = 'public.{names}'::regclass AND a.grantee <> c.relowner
       AND NOT (r.rolname = 'audli_api_runtime' AND a.privilege_type = 'SELECT');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION USING MESSAGE = 'Refusing: unexpected privileges on {names}: ' || bad;
    END IF;
END
$audli$;""", 'INSERT INTO listening_migrations(version, applied_at) VALUES (14, now());', 'COMMIT;']
    return '\n'.join(line.rstrip() for line in '\n\n'.join(lines).splitlines()) + '\n'


def postgres_listening_migration_015() -> str:
    """Daily challenge: the permanent per-day history and the operator pause switch (two additive tables). Alters nothing; the learner API only reads them."""
    dialect = postgresql.dialect()
    names = ', '.join(t.name for t in _SIXTH)
    lines = [
        '\n'.join(['-- Migration 015: Audli 2.0 daily challenge (two additive tables: listening_daily_challenges, listening_daily_control).',
        '-- Requires 009-012 and 014 (recorded in listening_migrations). Leaves schema_migrations untouched and alters no existing table.']),
        'BEGIN;', "SET LOCAL lock_timeout='5s';", 'LOCK TABLE public.listening_migrations IN EXCLUSIVE MODE;',
        """DO $$ BEGIN
 IF NOT (ARRAY[9, 10, 11, 12, 14] <@ ARRAY(SELECT version FROM public.listening_migrations)) THEN
  RAISE EXCEPTION 'Expected migrations 009, 010, 011, 012 and 014 first';
 END IF;
 IF 15 IN (SELECT version FROM public.listening_migrations) OR to_regclass('public.listening_daily_challenges') IS NOT NULL THEN
  RAISE EXCEPTION 'Migration 015 is already applied or partially present; review before retrying';
 END IF;
END $$;""",
    ]
    lines += [str(CreateTable(t).compile(dialect=dialect)).strip() + ';' for t in _SIXTH]
    for table in _SIXTH:
        lines += [str(CreateIndex(i).compile(dialect=dialect)) + ';' for i in sorted(table.indexes, key=lambda i: i.name)]
    for table in _SIXTH:
        lines += [f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;', f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC;']
    # Same fail-closed pattern as 014: strip the Supabase client roles, give the API role SELECT only, then assert the resulting privilege set per table.
    lines += [f"""DO $audli$
DECLARE client text;
BEGIN
    FOREACH client IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = client) THEN
            EXECUTE 'REVOKE ALL ON TABLE {names} FROM ' || quote_ident(client);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audli_api_runtime') THEN
        GRANT SELECT ON TABLE {names} TO audli_api_runtime;
        CREATE POLICY audli_api_runtime_listening_read ON listening_daily_challenges FOR SELECT TO audli_api_runtime USING (true);
        CREATE POLICY audli_api_runtime_listening_read ON listening_daily_control FOR SELECT TO audli_api_runtime USING (true);
    END IF;
END
$audli$;""", f"""DO $audli$
DECLARE bad text;
BEGIN
    SELECT string_agg(c.relname || '=' || coalesce(r.rolname, 'PUBLIC') || ':' || a.privilege_type, ', ') INTO bad
      FROM pg_class c
      CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
      LEFT JOIN pg_roles r ON r.oid = a.grantee
     WHERE c.oid IN ('public.listening_daily_challenges'::regclass, 'public.listening_daily_control'::regclass) AND a.grantee <> c.relowner
       AND NOT (r.rolname = 'audli_api_runtime' AND a.privilege_type = 'SELECT');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION USING MESSAGE = 'Refusing: unexpected privileges: ' || bad;
    END IF;
END
$audli$;""", 'INSERT INTO listening_migrations(version, applied_at) VALUES (15, now());', 'COMMIT;']
    return '\n'.join(line.rstrip() for line in '\n\n'.join(lines).splitlines()) + '\n'


def postgres_listening_rollback() -> str:
    """Remove EVERYTHING Audli 2.0 added (migrations 009-011): the tables, the guard functions and the ledger. Destroys all 2.0 learner data.
    Operator-only, never run by the application, and it refuses to run unless the session first says so in words:
        SET audli.confirm_listening_rollback = 'yes';                -- required
        SET audli.discard_learner_data = 'yes';                      -- also required if any 2.0 attempt exists
    1.x tables and data are not touched. To go back to 1.x WITHOUT losing 2.0 data, do not run this: turn the two feature flags off instead."""
    drops = [t.name for t in reversed(l.OWNED)]
    functions = ['listening_guard_version', 'listening_guard_children', 'listening_guard_audio', 'listening_guard_response', 'listening_guard_attempt']
    return '\n'.join([
        '-- Audli 2.0 rollback: removes migrations 009-011 completely. DESTROYS 2.0 learner data. Read docs/LISTENING-CUTOVER.md first.',
        'BEGIN;', "SET LOCAL lock_timeout='10s';",
        """DO $$ BEGIN
 IF coalesce(current_setting('audli.confirm_listening_rollback', true), '') <> 'yes' THEN
  RAISE EXCEPTION 'Refusing: run SET audli.confirm_listening_rollback = ''yes''; first, in this session';
 END IF;
 IF to_regclass('public.listening_attempts') IS NOT NULL
    AND (SELECT count(*) FROM public.listening_attempts) > 0
    AND coalesce(current_setting('audli.discard_learner_data', true), '') <> 'yes' THEN
  RAISE EXCEPTION 'Refusing: 2.0 attempts exist. To discard them run SET audli.discard_learner_data = ''yes''; first';
 END IF;
END $$;""",
        *[f'DROP TABLE IF EXISTS {name};' for name in drops],
        *[f'DROP FUNCTION IF EXISTS {name}();' for name in functions],
        'COMMIT;', ''])
