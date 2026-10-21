#!/usr/bin/env python3
"""LOCAL ONLY. Build a production-shaped schema-5 database (migrations 001-005 exactly as checked in) filled by the real
application code, to rehearse the migration tooling without production data. Refuses any non-loopback host.

  make_fixture.py --url-var VARIABLE --db-name NAME [--learners 60] [--seed 6]

VARIABLE holds an admin URL on a local server (the database NAME is created). Learner history is deliberately awkward:
never-started learners, one old lesson, several lessons on different days, two lessons inside one 24-hour window, stale paused
lessons, a lesson just started, and long histories.
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url-var', required=True)
    parser.add_argument('--db-name', required=True)
    parser.add_argument('--learners', type=int, default=60)
    parser.add_argument('--seed', type=int, default=6)
    args = parser.parse_args()
    url = make_url(os.environ[args.url_var].replace('postgresql+psycopg://', 'postgresql://', 1))
    if url.host not in ('localhost', '127.0.0.1', '::1'):
        sys.exit('make_fixture: refusing a non-loopback host')
    admin = url.set(drivername='postgresql').render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(f'CREATE DATABASE "{args.db_name}"')
    target = url.set(database=args.db_name)
    with psycopg.connect(target.set(drivername='postgresql').render_as_string(hide_password=False), autocommit=True) as db:
        for name in ('postgres.sql', 'postgres-002.sql', 'postgres-003.sql', 'postgres-004.sql', 'postgres-005.sql'):
            db.execute((REPO / 'docs' / name).read_text(), prepare=False)
    from app.lesson import Lesson
    from app.repository import PostgresRepository
    repo = PostgresRepository(target.render_as_string(hide_password=False))
    rng, now = random.Random(args.seed), datetime.now(timezone.utc)
    counts = {'never': 0, 'one_old': 0, 'cooldown': 0, 'two_in_one_day': 0, 'stale_paused': 0, 'just_started': 0, 'long_history': 0}

    def lesson(started, status='completed'):
        return Lesson(id=str(uuid4()), started_at=started, active_since=None if status != 'active' else started, name='Fixture',
                      focus='details', status='active', phase='WELCOME')

    def run(scoped, started, finish):
        item = lesson(started)
        scoped.create_lesson(item)
        if finish == 'completed':
            item.status, item.phase, item.completed_at, item.active_since = 'completed', 'COMPLETED', started + timedelta(minutes=9), None
            scoped.save_lesson(item, 0)
        elif finish == 'paused':
            item.status, item.active_since, item.elapsed_seconds = 'paused', None, 200
            scoped.save_lesson(item, 0)

    for index in range(args.learners):
        uid = str(UUID(f'00000000-0000-4000-8000-{index + 1:012d}'))
        scoped = repo.for_learner(uid)
        profile = scoped.profile()
        profile.name, profile.goal, profile.onboarding_status = f'Learner {index}', 'Follow meetings', 'complete'
        scoped.save_profile(profile)
        kind = ['never', 'one_old', 'cooldown', 'two_in_one_day', 'stale_paused', 'just_started', 'long_history'][index % 7]
        counts[kind] += 1
        if kind == 'one_old':
            run(scoped, now - timedelta(days=rng.randint(3, 10), hours=rng.randint(0, 20)), 'completed')
        elif kind == 'cooldown':
            for days in sorted({rng.randint(2, 9) for _ in range(3)}, reverse=True):
                run(scoped, now - timedelta(days=days, minutes=rng.randint(0, 600)), 'completed')
            run(scoped, now - timedelta(hours=rng.randint(1, 22), minutes=rng.randint(0, 59)), 'completed')
        elif kind == 'two_in_one_day':
            first = now - timedelta(hours=rng.randint(7, 20))
            run(scoped, first, 'completed')
            run(scoped, first + timedelta(hours=rng.randint(1, 5)), 'completed')
        elif kind == 'stale_paused':
            run(scoped, now - timedelta(days=rng.randint(2, 5), hours=rng.randint(0, 20)), 'paused')
        elif kind == 'just_started':
            run(scoped, now - timedelta(minutes=5), 'active')
        elif kind == 'long_history':
            for days in range(rng.randint(10, 14), 0, -1):
                run(scoped, now - timedelta(days=days, hours=rng.randint(0, 5)), 'completed')
        if kind != 'never' and index % 3 == 0:
            scoped.cache_lesson_audio(f'fixture-{index}.mp3', os.urandom(30_000))
    repo.close()
    print(f'fixture ready: database {args.db_name}, {args.learners} learners, kinds {counts}')


main()
