"""Least-privilege database role for the spoken-answer WORKER (the one process that holds the AI provider key).

The 2.0 tables have row-level security enabled and no policies (migrations 009-011), so a non-owner role sees nothing from GRANTs alone: each
privilege below is paired with a policy for that role. The role gets only what the worker's code actually does:
  read    versions, exercises, rubrics (the lesson and the key points it asks the model about), spoken, jobs, ai_limits
  update  spoken, jobs, ai_limits          insert  ai_limits (a new day's row)
and nothing else: no DELETE anywhere, no answer keys, no attempts, responses, answers or learner accounts, and no 1.x table.
The API process keeps running as the same role as 1.x (it shares the process); confining it needs the claims-mode design and is out of scope here.
Generated like the migrations: `docs/postgres-listening-worker-role.sql` is this function's output for the example role name.
"""
SELECT = ('listening_lesson_versions', 'listening_exercises', 'listening_exercise_rubrics', 'listening_spoken', 'listening_jobs', 'listening_ai_limits')
UPDATE = ('listening_spoken', 'listening_jobs', 'listening_ai_limits')
INSERT = ('listening_ai_limits',)
EXAMPLE_ROLE = 'audli_listening_worker'


def worker_role_sql(role: str = EXAMPLE_ROLE, policies: bool = True) -> str:
    if not role.replace('_', '').isalnum() or not role[0].isalpha() or len(role) > 40:
        raise ValueError('Role names are letters, digits and underscores')
    lines = [f'-- Least-privilege role for the Audli 2.0 spoken-answer worker. Run once as the database owner AFTER migrations 009-011.',
             f'-- Create the role yourself first (a LOGIN role with its own password, not shared with anything else): CREATE ROLE {role} LOGIN PASSWORD \'...\';',
             'BEGIN;', f'GRANT USAGE ON SCHEMA public TO {role};']
    lines += [f'GRANT SELECT ON {", ".join(SELECT)} TO {role};', f'GRANT UPDATE ON {", ".join(UPDATE)} TO {role};', f'GRANT INSERT ON {", ".join(INSERT)} TO {role};']
    if policies:
        for table in SELECT:
            lines += [f'DROP POLICY IF EXISTS {role}_select ON {table};', f'CREATE POLICY {role}_select ON {table} FOR SELECT TO {role} USING (true);']
        for table in UPDATE:
            lines += [f'DROP POLICY IF EXISTS {role}_update ON {table};', f'CREATE POLICY {role}_update ON {table} FOR UPDATE TO {role} USING (true) WITH CHECK (true);']
        for table in INSERT:
            lines += [f'DROP POLICY IF EXISTS {role}_insert ON {table};', f'CREATE POLICY {role}_insert ON {table} FOR INSERT TO {role} WITH CHECK (true);']
    lines.append('COMMIT;')
    return '\n'.join(lines) + '\n'


# ---- the lesson GENERATOR role (Milestone 05) ------------------------------------------------------------------------------------------
# The generator writes new lessons and its own request records; it can never touch learner data (attempts, responses, answers, spoken answers,
# recordings, accounts) and never deletes anything. It is a different role from the spoken-answer worker on purpose: each holds only the provider
# credentials and database rights its own job needs.
GENERATOR_SELECT = ('listening_lessons', 'listening_lesson_versions', 'listening_audio_variants', 'listening_exercises', 'listening_exercise_keys',
                    'listening_exercise_rubrics', 'listening_lesson_owners', 'listening_generation_requests', 'listening_generation_limits',
                    'listening_jobs', 'listening_ai_limits')
GENERATOR_INSERT = ('listening_lessons', 'listening_lesson_versions', 'listening_audio_variants', 'listening_exercises', 'listening_exercise_keys',
                    'listening_exercise_rubrics', 'listening_lesson_owners', 'listening_generation_limits', 'listening_ai_limits')
GENERATOR_UPDATE = ('listening_lesson_versions', 'listening_generation_requests', 'listening_generation_limits', 'listening_jobs')
EXAMPLE_GENERATOR_ROLE = 'audli_listening_generator'


def generator_role_sql(role: str = EXAMPLE_GENERATOR_ROLE) -> str:
    if not role.replace('_', '').isalnum() or not role[0].isalpha() or len(role) > 40:
        raise ValueError('Role names are letters, digits and underscores')
    lines = ['-- Least-privilege role for the Audli 2.0 lesson generator. Run once as the database owner AFTER migration 012.',
             f"-- Create the role yourself first (a LOGIN role with its own password, shared with nothing else): CREATE ROLE {role} LOGIN PASSWORD '...';",
             'BEGIN;', f'GRANT USAGE ON SCHEMA public TO {role};',
             f'GRANT SELECT ON {", ".join(GENERATOR_SELECT)} TO {role};', f'GRANT INSERT ON {", ".join(GENERATOR_INSERT)} TO {role};',
             f'GRANT UPDATE ON {", ".join(GENERATOR_UPDATE)} TO {role};']
    for table in GENERATOR_SELECT:
        lines += [f'DROP POLICY IF EXISTS {role}_select ON {table};', f'CREATE POLICY {role}_select ON {table} FOR SELECT TO {role} USING (true);']
    for table in GENERATOR_INSERT:
        lines += [f'DROP POLICY IF EXISTS {role}_insert ON {table};', f'CREATE POLICY {role}_insert ON {table} FOR INSERT TO {role} WITH CHECK (true);']
    for table in GENERATOR_UPDATE:
        lines += [f'DROP POLICY IF EXISTS {role}_update ON {table};', f'CREATE POLICY {role}_update ON {table} FOR UPDATE TO {role} USING (true) WITH CHECK (true);']
    lines.append('COMMIT;')
    return '\n'.join(lines) + '\n'


# ---- the API role ---------------------------------------------------------------------------------------------------------------
# Hosted Supabase runs the 1.x API as a NON-owner role (audli_api_runtime) with one `FOR ALL` policy per table, and Supabase's default
# privileges also hand every new public table to anon/authenticated/service_role. Migrations 009-011 enable RLS and add no policies, so
# without this step the 2.0 API sees no rows in production, and the Supabase client roles keep table privileges they must never have.
# Run once as the database owner AFTER migrations 009-011 (and again after any later listening migration that adds a table).
API_TABLES = ('listening_lessons', 'listening_lesson_versions', 'listening_audio_variants', 'listening_exercises', 'listening_exercise_keys',
              'listening_exercise_rubrics', 'listening_attempts', 'listening_answers', 'listening_responses', 'listening_spoken',
              'listening_jobs', 'listening_ai_limits', 'listening_lesson_owners', 'listening_generation_requests', 'listening_generation_limits', 'listening_migrations')
LIBRARY_TABLE = 'listening_library_entries'       # read-only for the API; written only by the operator workflow (app/listening/library.py)
DAILY_TABLES = ('listening_daily_challenges', 'listening_daily_control')      # read-only for the API; written only by the daily process
EXAMPLE_API_ROLE = 'audli_api_runtime'
CLIENT_ROLES = ('anon', 'authenticated', 'service_role')


def api_role_sql(role: str = EXAMPLE_API_ROLE) -> str:
    if not role.replace('_', '').isalnum() or not role[0].isalpha() or len(role) > 40:
        raise ValueError('Role names are letters, digits and underscores')
    tables = ', '.join(API_TABLES)
    lines = ['-- Grants and row-level-security policies for the Audli 2.0 API on a host where it runs as a non-owner role (Supabase).',
             '-- Run once as the database owner AFTER migrations 009-011. Read, insert and update only: the API never removes 2.0 rows (account erasure',
             '-- cascades from users through foreign keys, which run as the table owner).',
             'BEGIN;', f'GRANT USAGE ON SCHEMA public TO {role};', f'GRANT SELECT, INSERT, UPDATE ON {tables} TO {role};']
    for client in CLIENT_ROLES:
        lines.append(f'REVOKE ALL ON {tables} FROM {client};')
    for table in API_TABLES:
        lines += [f'DROP POLICY IF EXISTS {role}_listening ON {table};', f'CREATE POLICY {role}_listening ON {table} FOR ALL TO {role} USING (true) WITH CHECK (true);']
    # The curated-library workflow table (migration 014) is operator-written: the API may only READ lesson categories for the catalog. Conditional, so this
    # script is safe to run before or after 014 and never fails because the table does not exist yet.
    lines += [f"""DO $audli$
BEGIN
    IF to_regclass('public.{LIBRARY_TABLE}') IS NOT NULL THEN
        GRANT SELECT ON {LIBRARY_TABLE} TO {role};
        REVOKE ALL ON {LIBRARY_TABLE} FROM {', '.join(CLIENT_ROLES)};
        DROP POLICY IF EXISTS {role}_listening_read ON {LIBRARY_TABLE};
        CREATE POLICY {role}_listening_read ON {LIBRARY_TABLE} FOR SELECT TO {role} USING (true);
    END IF;
END
$audli$;"""]
    # Same for the Daily Challenge tables (migration 015): the API only reads them (today's card, keeping a challenge out of Discover until its day is over).
    for table in DAILY_TABLES:
        lines += [f"""DO $audli$
BEGIN
    IF to_regclass('public.{table}') IS NOT NULL THEN
        GRANT SELECT ON {table} TO {role};
        REVOKE ALL ON {table} FROM {', '.join(CLIENT_ROLES)};
        DROP POLICY IF EXISTS {role}_listening_read ON {table};
        CREATE POLICY {role}_listening_read ON {table} FOR SELECT TO {role} USING (true);
    END IF;
END
$audli$;"""]
    lines.append('COMMIT;')
    return '\n'.join(lines) + '\n'


# ---- the DAILY CHALLENGE role -------------------------------------------------------------------------------------------------------
# The only process allowed to publish a lesson automatically. It is the curator workflow (app/listening/library.py) run by a service account, so it needs
# exactly what register / approve / publish need (including DELETE on lesson owners, which the API and the generator deliberately lack) plus its own two tables
# and the generation bookkeeping used to submit one request per attempt. It has NO learner data (attempts, answers, spoken, recordings, accounts are not
# granted) and holds neither the speech key nor any bucket credential.
DAILY_SELECT = ('listening_lessons', 'listening_lesson_versions', 'listening_audio_variants', 'listening_exercises', 'listening_exercise_keys',
                'listening_exercise_rubrics', 'listening_lesson_owners', 'listening_generation_requests', 'listening_generation_limits', 'listening_jobs',
                'listening_ai_limits', 'listening_library_entries', 'listening_daily_challenges', 'listening_daily_control')
DAILY_INSERT = ('listening_generation_requests', 'listening_generation_limits', 'listening_jobs', 'listening_library_entries', 'listening_daily_challenges',
                'listening_daily_control')
DAILY_UPDATE = ('listening_generation_requests', 'listening_generation_limits', 'listening_jobs', 'listening_library_entries', 'listening_daily_challenges',
                'listening_daily_control')
DAILY_DELETE = ('listening_lesson_owners',)
EXAMPLE_DAILY_ROLE = 'audli_listening_daily'


def daily_role_sql(role: str = EXAMPLE_DAILY_ROLE) -> str:
    if not role.replace('_', '').isalnum() or not role[0].isalpha() or len(role) > 40:
        raise ValueError('Role names are letters, digits and underscores')
    lines = ['-- Least-privilege role for the Audli 2.0 daily-challenge scheduler. Run once as the database owner AFTER migrations 012, 014 and 015.',
             f"-- Create the role yourself first (a LOGIN role with its own password, shared with nothing else): CREATE ROLE {role} LOGIN PASSWORD '...';",
             'BEGIN;', f'GRANT USAGE ON SCHEMA public TO {role};',
             f'GRANT SELECT ON {", ".join(DAILY_SELECT)} TO {role};', f'GRANT INSERT ON {", ".join(DAILY_INSERT)} TO {role};',
             f'GRANT UPDATE ON {", ".join(DAILY_UPDATE)} TO {role};', f'GRANT DELETE ON {", ".join(DAILY_DELETE)} TO {role};']
    for verb, tables, clause in (('select', DAILY_SELECT, 'FOR SELECT TO {r} USING (true)'), ('insert', DAILY_INSERT, 'FOR INSERT TO {r} WITH CHECK (true)'),
                                 ('update', DAILY_UPDATE, 'FOR UPDATE TO {r} USING (true) WITH CHECK (true)'), ('delete', DAILY_DELETE, 'FOR DELETE TO {r} USING (true)')):
        for table in tables:
            lines += [f'DROP POLICY IF EXISTS {role}_{verb} ON {table};', f'CREATE POLICY {role}_{verb} ON {table} ' + clause.format(r=role) + ';']
    lines.append('COMMIT;')
    return '\n'.join(lines) + '\n'
