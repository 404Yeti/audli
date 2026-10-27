"""Disposable loopback database utilities, synthetic owners only."""
from contextlib import contextmanager
from pathlib import Path
import sys
import uuid
import asyncio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from bench_turns import prepare_database
from bench_app import learner_uuid
from sqlalchemy.engine import make_url
from app.repository import PostgresRepository
from app.models import Conversation
from app.services.demo import DemoProvider
import psycopg
from psycopg import sql

ADMIN = 'postgresql://audli_poc:local-only@127.0.0.1:55449/postgres'


@contextmanager
def database(tokens=('bench-a', 'bench-b'), admin=ADMIN):
    if make_url(admin).host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Loopback only')
    name = 'audli_claims_' + uuid.uuid4().hex
    try:
        url = prepare_database(admin, name, tokens)
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def seed(url, token):
    repo = PostgresRepository(url, learner_uuid(token))
    profile = repo.profile()
    exercise = str(uuid.uuid4())
    content = asyncio.run(DemoProvider().generate(profile))
    repo.save_exercise(exercise, content, profile.difficulty, exercise + '.wav')
    repo.save_conversation(Conversation(exercise_id=exercise, state='AWAITING_SUMMARY'))
    return repo, exercise
