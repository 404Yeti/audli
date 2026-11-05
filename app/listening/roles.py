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
