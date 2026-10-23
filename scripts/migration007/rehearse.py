#!/usr/bin/env python3
"""LOCAL ONLY. Rehearse migration 007 (AUD-77, review_improvements) end to end on a disposable PostgreSQL. Never touches production.

  AUDLI_REHEARSAL_DATABASE_URL=postgresql://user:pw@127.0.0.1:PORT/postgres  rehearse.py [--learners 60] [--keep] [--out FILE]

What it does, in order (each step is recorded as pass/fail in the report; any failure exits non-zero):
  1. Builds a production-shaped schema-6 database with the real application code (scripts/migration006/make_fixture.py, then
     docs/postgres-006.sql exactly as checked in) and adds synthetic exercises and attempts that review rows can reference.
  2. Fingerprints every table (row count + md5 over the rows), takes a pg_dump -Fc backup, restores it into a NEW database and proves
     the restore is exact. All later work happens on that verified restored copy.
  3. Applies docs/postgres-007.sql with the production timeouts; proves existing lesson/entitlement data is unchanged; checks the
     new table's columns, row-level security and privileges (anon, authenticated, PUBLIC); exercises its constraints with real rows.
  4. Re-applies 007 (must fail atomically), then rolls back: a populated table must be REFUSED without the exact authorization
     phrase (nothing may change), succeeds with it, the discarded rows come back from a table backup, an empty table needs no phrase.
  5. Restores a post-007 dump, and checks the application's own schema gates (flag on fails closed at 6, passes at 7).

The report holds counts, hashes, timings and pass/fail only: no connection strings, no passwords, no learner data (all synthetic).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402

URL_VAR = 'AUDLI_REHEARSAL_DATABASE_URL'
FORBIDDEN = ('AUDLI_PROD_DATABASE_URL', 'AUDLI_DATABASE_URL', 'DATABASE_URL', 'SUPABASE_URL', 'SUPABASE_DB_URL', 'SUPABASE_ACCESS_TOKEN',
             'AUDLI_SUPABASE_URL', 'AUDLI_SUPABASE_SERVICE_ROLE_KEY')
PHRASE = 'DROP POPULATED review_improvements'
EXPECTED_COLUMNS = ['id', 'user_id', 'exercise_id', 'attempt_id', 'status', 'source_hash', 'improved_text', 'notes', 'rejection', 'model',
                    'repaired', 'prompt_version', 'claimed_at', 'finished_at']
PG_BIN = Path('/usr/lib/postgresql/17/bin')


def tool(name):
    return str(PG_BIN / name) if (PG_BIN / name).exists() else (shutil.which(name) or sys.exit(f'missing tool: {name}'))


class Rehearsal:
    def __init__(self, admin_url):
        self.admin = urlparse(admin_url)
        self.results, self.dbs = [], []
        self.scratch = Path(tempfile.mkdtemp(prefix='audli-r007-'))
        os.chmod(self.scratch, 0o700)

    # ------------------------------------------------------------------ plumbing
    def env(self):
        return {**os.environ, 'PGHOST': self.admin.hostname, 'PGPORT': str(self.admin.port or 5432), 'PGUSER': unquote(self.admin.username or ''),
                'PGPASSWORD': unquote(self.admin.password or ''), 'PGCONNECT_TIMEOUT': '10'}

    def dsn(self, db):
        return f'postgresql://{self.admin.username}:{self.admin.password}@{self.admin.hostname}:{self.admin.port or 5432}/{db}'

    def connect(self, db='postgres', autocommit=True):
        return psycopg.connect(self.dsn(db), autocommit=autocommit)

    def run(self, argv, check=True, input_text=None):
        done = subprocess.run(argv, env=self.env(), capture_output=True, text=True, input=input_text, timeout=600)
        if check and done.returncode:
            raise RuntimeError(f'{Path(argv[0]).name} failed ({done.returncode}): {done.stderr.strip()[-300:]}')
        return done

    def psql(self, db, file=None, sets=(), command=None):
        """Apply SQL the way an operator would: ON_ERROR_STOP, production timeouts, same session for every -c/-f."""
        argv = [tool('psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-d', db, '-c', "SET lock_timeout = '5s'", '-c', "SET statement_timeout = '30s'"]
        for statement in sets:
            argv += ['-c', statement]
        if command:
            argv += ['-c', command]
        if file:
            argv += ['-f', str(file)]
        return self.run(argv, check=False)

    def restore(self, db, dump, *extra):
        """pg_restore into DB. The only tolerated message is the dump's own CREATE SCHEMA public (the schema exists in every database);
        anything else on stderr is a failed restore."""
        done = self.run([tool('pg_restore'), '--no-owner', *extra, '-d', db, str(dump)], check=False)
        problems = [line for line in done.stderr.splitlines() if line.strip() and not (
            'schema "public" already exists' in line or line.startswith('Command was: CREATE SCHEMA public') or 'errors ignored on restore: 1' in line)]
        return not problems, ' | '.join(problems)[-300:]

    def new_db(self, name):
        with self.connect() as db:
            db.execute(f'CREATE DATABASE "{name}"')
        self.dbs.append(name)
        return name

    def scalar(self, db, sql, params=None):
        with self.connect(db) as conn:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None

    def record(self, name, ok, detail=''):
        self.results.append({'step': name, 'ok': bool(ok), 'detail': detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f' - {detail}' if detail else ''), flush=True)
        return ok

    # ------------------------------------------------------------------ database facts
    def versions(self, db):
        with self.connect(db) as conn:
            return [r[0] for r in conn.execute('SELECT version FROM schema_migrations ORDER BY version')]

    def has_table(self, db, name='review_improvements'):
        return bool(self.scalar(db, "SELECT to_regclass(%s) IS NOT NULL", (f'public.{name}',)))

    def snapshot(self, db):
        """{table: (row count, md5 over the rows in a stable order)} for every table in public."""
        out = {}
        with self.connect(db) as conn:
            tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]
            for table in tables:
                count, digest = conn.execute(f'SELECT count(*), md5(coalesce(string_agg(t::text, E\'\\n\' ORDER BY t::text), \'\')) FROM "{table}" t').fetchone()
                out[table] = (count, digest)
        return out

    def drop_all(self):
        with self.connect() as db:
            for name in self.dbs:
                db.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--learners', type=int, default=60)
    parser.add_argument('--keep', action='store_true', help='keep the rehearsal databases and scratch directory')
    parser.add_argument('--out')
    args = parser.parse_args()

    present = [name for name in FORBIDDEN if os.environ.get(name)]
    if present:
        sys.exit(f'REFUSED: production or database variables are set in this environment ({", ".join(present)}); unset them first.')
    admin_url = os.environ.get(URL_VAR)
    if not admin_url:
        sys.exit(f'{URL_VAR} is not set (a disposable loopback PostgreSQL 17).')
    host = urlparse(admin_url).hostname or ''
    if host not in ('127.0.0.1', 'localhost', '::1') or 'supabase' in admin_url.lower():
        sys.exit('REFUSED: the rehearsal server must be a loopback host and must not look like Supabase.')

    r = Rehearsal(admin_url)
    finished = False
    stamp = datetime.now(timezone.utc).strftime('%H%M%S')
    migration, rollback = REPO / 'docs' / 'postgres-007.sql', REPO / 'docs' / 'postgres-007-rollback.sql'
    report = {'migration_sha256': hashlib.sha256(migration.read_bytes()).hexdigest(), 'rollback_sha256': hashlib.sha256(rollback.read_bytes()).hexdigest(),
              'started_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    try:
        server = r.scalar('postgres', 'SHOW server_version')
        report['server_version'] = server
        r.record('rehearsal server is PostgreSQL 17 on loopback', server.startswith('17'), server)
        with r.connect() as conn:  # Supabase-style roles, so the REVOKE branches of 007 are really exercised.
            for role in ('anon', 'authenticated', 'service_role'):
                conn.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")

        # 1. production-shaped schema 6 ---------------------------------------------------------------------------------------------
        src = f'audli_r007_src_{stamp}'
        done = subprocess.run([sys.executable, str(REPO / 'scripts' / 'migration006' / 'make_fixture.py'), '--url-var', URL_VAR, '--db-name', src,
                               '--learners', str(args.learners)], env=r.env() | {URL_VAR: admin_url}, capture_output=True, text=True, timeout=900)
        r.dbs.append(src)
        if not r.record('fixture built by the real application code (schema 5)', done.returncode == 0, done.stderr.strip()[-200:] if done.returncode else ''):
            raise SystemExit(1)
        applied = r.psql(src, file=REPO / 'docs' / 'postgres-006.sql')
        r.record('migration 006 applied exactly as checked in (fixture is now schema 6)', applied.returncode == 0 and r.versions(src) == [1, 2, 3, 4, 5, 6],
                 applied.stderr.strip()[-200:])
        with r.connect(src) as conn:  # synthetic exercises + attempts for review rows to reference
            users = [row[0] for row in conn.execute('SELECT id FROM users ORDER BY id LIMIT 6')]
            for index, user in enumerate(users):
                session = str(uuid4())
                conn.execute("INSERT INTO training_sessions (id, user_id, status, started_at, starting_difficulty) VALUES (%s, %s, 'active', now(), '{}')", (session, user))
                for number in range(2):
                    exercise, attempt = str(uuid4()), str(uuid4())
                    conn.execute("INSERT INTO exercises (id, user_id, session_id, content, difficulty, audio_name, created_at) VALUES (%s, %s, %s, '{}', '{}', %s, now())",
                                 (exercise, user, session, f'rehearsal-{index}-{number}.mp3'))
                    conn.execute("INSERT INTO attempts (id, exercise_id, transcription, recognized_text, confidence, transcription_source, status, created_at) VALUES (%s, %s, '{}', %s, 0.93, 'openai', 'evaluated', now())",
                                 (attempt, exercise, 'we meets on friday'))
        before = r.snapshot(src)
        r.record('schema 6 fingerprint taken (every table: rows + md5)', 'lesson_entitlements' in before and before['lesson_entitlements'][0] > 0 and before['lesson_lifecycles'][0] > 0 and 'review_improvements' not in before,
                 f"{len(before)} tables, {before['lesson_lifecycles'][0]} lessons, {before['lesson_entitlements'][0]} entitlement rows")
        report['tables_at_schema_6'] = {t: c for t, (c, _) in before.items()}

        # 2. preflight, backup, verified restore ------------------------------------------------------------------------------------
        long_tx = r.scalar(src, "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND xact_start < now() - interval '1 minute'")
        r.record('preflight: versions exactly 1-6, review_improvements absent, no long transactions',
                 r.versions(src) == [1, 2, 3, 4, 5, 6] and not r.has_table(src) and long_tx == 0)
        dump = r.scratch / 'schema6.dump'
        r.run([tool('pg_dump'), '-Fc', '--schema=public', '-f', str(dump), src])
        toc = r.run([tool('pg_restore'), '-l', str(dump)]).stdout.count('TABLE DATA')
        report['backup'] = {'bytes': dump.stat().st_size, 'sha256': hashlib.sha256(dump.read_bytes()).hexdigest(), 'table_data_entries': toc}
        r.record('backup taken (pg_dump -Fc)', dump.stat().st_size > 0 and toc >= len(before) - 1, f"{dump.stat().st_size} bytes, {toc} data entries")
        work = r.new_db(f'audli_r007_work_{stamp}')
        clean, problems = r.restore(work, dump)
        after_restore = r.snapshot(work)
        r.record('restore is exact: no restore errors; every table count and md5 equals the source; versions 1-6',
                 clean and after_restore == before and r.versions(work) == [1, 2, 3, 4, 5, 6], problems)

        # 3. apply 007 on the verified restored copy --------------------------------------------------------------------------------
        started = time.perf_counter()
        applied = r.psql(work, file=migration)
        seconds = round(time.perf_counter() - started, 2)
        report['apply_seconds'] = seconds
        r.record('migration 007 applies (lock_timeout 5s, statement_timeout 30s)', applied.returncode == 0 and r.versions(work) == [1, 2, 3, 4, 5, 6, 7], f'{seconds}s')
        migrated = r.snapshot(work)
        unchanged = {t: v for t, v in before.items() if t != 'schema_migrations' and migrated.get(t) != v}
        r.record('existing data intact: every pre-existing table has identical rows (incl. lesson_lifecycles and lesson_entitlements)',
                 not unchanged and migrated['review_improvements'][0] == 0 and set(migrated) - set(before) == {'review_improvements'}, f'changed: {sorted(unchanged)}' if unchanged else '')
        r.record('the only other change is exactly one new schema_migrations row (version 7)',
                 migrated['schema_migrations'][0] == before['schema_migrations'][0] + 1 and r.versions(work) == [1, 2, 3, 4, 5, 6, 7])
        columns = [row[0] for row in r.connect(work).execute("SELECT column_name FROM information_schema.columns WHERE table_name='review_improvements' ORDER BY ordinal_position")]
        r.record('review_improvements has exactly the expected columns', columns == EXPECTED_COLUMNS, str(columns) if columns != EXPECTED_COLUMNS else '')
        rls = r.scalar(work, "SELECT relrowsecurity FROM pg_class WHERE relname='review_improvements'")
        grants = r.scalar(work, "SELECT count(*) FROM information_schema.role_table_grants WHERE table_name='review_improvements' AND grantee IN ('PUBLIC','anon','authenticated')")
        probes = [r.scalar(work, "SELECT has_table_privilege(%s, 'review_improvements', 'SELECT,INSERT,UPDATE,DELETE')", (role,)) for role in ('anon', 'authenticated')]
        r.record('private: row-level security on, no grants for PUBLIC/anon/authenticated', rls is True and grants == 0 and probes == [False, False], f'rls={rls} grants={grants} probes={probes}')

        with r.connect(work) as conn:  # behaviour with real learner / exercise / attempt ids
            rows = conn.execute('SELECT e.user_id, e.id, a.id FROM exercises e JOIN attempts a ON a.exercise_id = e.id WHERE e.audio_name LIKE %s ORDER BY e.id LIMIT 3', ('rehearsal-%',)).fetchall()
        (user1, ex1, at1), (_, ex2, at2), (user3, ex3, at3) = rows

        def insert(user, exercise, attempt, status, text='better', notes='[]', rid=None):
            with r.connect(work) as conn:
                try:
                    conn.execute('INSERT INTO review_improvements (id, user_id, exercise_id, attempt_id, status, source_hash, improved_text, notes, prompt_version, claimed_at) '
                                 'VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now())',
                                 (rid or str(uuid4()), user, exercise, attempt, status, 'h' * 64, text if status == 'ready' else None, notes if status == 'ready' else None, 'v2'))
                    return None
                except psycopg.Error as error:
                    return error.sqlstate
        ok = [insert(user1, ex1, at1, 'ready'), insert(user3, ex3, at3, 'failed'), insert(user3, ex3, at3, 'failed'), insert(user3, ex3, at3, 'abandoned'),
              insert(user3, ex3, at3, 'rejected'), insert(user3, ex3, at3, 'rejected'), insert(user3, ex3, at3, 'pending')]
        r.record('valid rows insert: one ready, repeated failed/abandoned/rejected history, one pending on another exercise', ok == [None] * 7, str(ok))
        bad = {'second live row on the same exercise (partial unique index)': (insert(user1, ex1, at1, 'pending'), '23505'),
               'ready second to a pending on the same exercise': (insert(user3, ex3, at3, 'ready'), '23505'),
               'ready row without text': (insert(user1, ex2, at2, 'ready', text=None, notes=None), '23514'),
               'unknown status': (insert(user1, ex2, at2, 'bogus'), '23514'),
               'unknown learner (foreign key)': (insert(str(uuid4()), ex2, at2, 'failed'), '23503'),
               'unknown exercise (foreign key)': (insert(user1, str(uuid4()), at2, 'failed'), '23503')}
        for label, (got, expected) in bad.items():
            r.record(f'constraint rejects: {label}', got == expected, f'sqlstate {got}')
        populated = r.scalar(work, 'SELECT count(*) FROM review_improvements')
        used = r.scalar(work, "SELECT count(*) FROM pg_indexes WHERE tablename='review_improvements' AND indexname IN ('improvements_by_learner_claim','one_active_improvement_per_exercise')")
        r.record('both indexes exist and the rejected inserts left no rows behind', used == 2 and populated == 7, f'rows={populated}')

        # 4. re-apply is atomic; the rollback guard; table backup; restoration of the discarded rows ---------------------------------
        state = r.snapshot(work)
        again = r.psql(work, file=migration)
        r.record('re-applying 007 fails cleanly and changes nothing (atomic)', again.returncode != 0 and r.snapshot(work) == state and r.versions(work)[-1] == 7)
        refusals = {}
        for label, sets in (('no authorization', ()), ('wrong phrase', ("SET audli.allow_destructive_rollback = 'drop populated review_improvements'",)),
                            ('partial phrase', ("SET audli.allow_destructive_rollback = 'DROP review_improvements'",))):
            refused = r.psql(work, file=rollback, sets=sets)
            refusals[label] = refused.returncode != 0 and 'refusing to drop it' in refused.stderr
            intact = r.snapshot(work) == state and r.versions(work)[-1] == 7 and r.has_table(work)
            r.record(f'rollback REFUSES a populated table: {label}', refusals[label] and intact, 'table, 7 rows and version 7 all intact' if intact else 'STATE CHANGED')
        table_dump = r.scratch / 'review_improvements.dump'
        r.run([tool('pg_dump'), '-Fc', '-t', 'public.review_improvements', '-f', str(table_dump), work])
        rolled = r.psql(work, file=rollback, sets=(f"SET audli.allow_destructive_rollback = '{PHRASE}'",))
        gone = rolled.returncode == 0 and not r.has_table(work) and r.versions(work) == [1, 2, 3, 4, 5, 6]
        back = r.snapshot(work)
        r.record('rollback with the exact authorization succeeds; schema returns to 6 and every other table equals the pre-007 fingerprint', gone and back == before, rolled.stderr.strip()[-200:])
        reapplied = r.psql(work, file=migration)
        rows_ok, rows_problems = r.restore(work, table_dump, '--data-only')
        r.record('discarded rows are restored from the table backup after re-applying 007', reapplied.returncode == 0 and rows_ok and r.snapshot(work)['review_improvements'] == state['review_improvements'],
                 rows_problems)
        with r.connect(work) as conn:
            conn.execute('DELETE FROM review_improvements')
        empty = r.psql(work, file=rollback)
        r.record('an EMPTY table rolls back without any authorization', empty.returncode == 0 and not r.has_table(work) and r.versions(work) == [1, 2, 3, 4, 5, 6] and r.snapshot(work) == before)

        # 5. restoration of a post-007 dump, and the application's own schema gates -------------------------------------------------
        r.psql(work, file=migration)
        insert(user1, ex1, at1, 'ready')
        v7_dump = r.scratch / 'schema7.dump'
        r.run([tool('pg_dump'), '-Fc', '--schema=public', '-f', str(v7_dump), work])
        v7_state = r.snapshot(work)
        v7_copy = r.new_db(f'audli_r007_v7_{stamp}')
        clean7, problems7 = r.restore(v7_copy, v7_dump)
        r.record('a post-007 backup restores exactly (table, rows and versions 1-7)', clean7 and r.snapshot(v7_copy) == v7_state and r.versions(v7_copy) == [1, 2, 3, 4, 5, 6, 7], problems7)
        pre = r.new_db(f'audli_r007_pre_{stamp}')
        clean6, problems6 = r.restore(pre, dump)
        r.record('the original pre-007 backup still restores exactly (production-equivalent recovery path)', clean6 and r.snapshot(pre) == before and r.versions(pre) == [1, 2, 3, 4, 5, 6], problems6)

        gate = subprocess.run([sys.executable, '-c', GATE_CHECK, r.dsn(pre), r.dsn(v7_copy)], env=r.env() | {'AUDLI_CI_OFFLINE': '1'}, capture_output=True, text=True, timeout=300,
                              cwd=REPO)
        r.record("application gates: schema 6 + flag on fails closed; schema 6 + flag off starts; schema 7 + flag on starts; lesson-limit gate passes at 6 and 7",
                 gate.returncode == 0 and gate.stdout.strip().endswith('GATES OK'), (gate.stdout + gate.stderr).strip()[-300:] if gate.returncode else '')
        finished = True
    except Exception:  # never let the summary hide what went wrong
        import traceback
        traceback.print_exc()
    finally:
        failed = [x for x in r.results if not x['ok']]
        if not finished:
            failed.append({'step': 'rehearsal did not run to completion', 'ok': False, 'detail': 'see the traceback above'})
        report.update({'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'steps': r.results, 'completed': finished, 'passed': sum(x['ok'] for x in r.results), 'failed': len(failed)})
        if not args.keep:
            r.drop_all()
            shutil.rmtree(r.scratch, ignore_errors=True)
        text = json.dumps(report, indent=2)
        if args.out:
            Path(args.out).write_text(text + '\n')
        print(f"\nREHEARSAL {'FAILED' if failed else 'PASSED'}: {report['passed']} passed, {report['failed']} failed")
        if failed:
            sys.exit(1)


GATE_CHECK = r'''
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests")
from app.config import Settings
from app.main import create_app
from app.repository import PostgresRepository
from conftest import FakeProvider
v6, v7 = sys.argv[1], sys.argv[2]
tmp = Path(tempfile.mkdtemp())
def start(url, flag, sub):
    repo = PostgresRepository(url)
    try:
        create_app(Settings(data_dir=tmp / sub, review_improvement_enabled=flag, _env_file=None), FakeProvider(), repo)
    finally:
        repo.close()
for url in (v6, v7):   # the AUD-47 lesson-limit schema gate must be satisfied at both schema versions
    gate = PostgresRepository(url)
    try:
        gate.require_lesson_limit_schema()
    finally:
        gate.close()
start(v6, False, "a")
try:
    start(v6, True, "b"); raise SystemExit("flag on at schema 6 should have failed")
except RuntimeError as error:
    assert "migration 007" in str(error), error
start(v7, True, "c")
start(v7, False, "d")
print("GATES OK")
'''

if __name__ == '__main__':
    main()
