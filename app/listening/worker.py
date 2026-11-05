"""Separate worker process for the spoken exercise:  python -m app.listening.worker   (add --once to drain and exit).

It is a different process from the API on purpose: transcription and assessment can be slow or fail without ever holding up a request,
and a restart of either side loses nothing, because jobs, leases and results live in the database. Run as many workers as you like;
the database hands each job to exactly one at a time and fences out any worker that lost its lease.
"""
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from app.listening import jobs, spoken
from app.storage.repository import utcnow

log = logging.getLogger('audli.listening.worker')


def make_engine(settings):
    """The worker's database engine. On PostgreSQL it connects directly and does NOT go through the 1.x repository, whose start-up validates the whole
    1.x schema (a role confined to the 2.0 tables cannot, and should not, read that). SQLite (local development) keeps the shared repository."""
    if settings.persistence == 'postgres':
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        return create_engine(make_url(settings.database_url.get_secret_value()).set(drivername='postgresql+psycopg'), pool_pre_ping=True, pool_size=2,
                             max_overflow=0, pool_timeout=10, connect_args={'connect_timeout': 10, 'prepare_threshold': None,
                                                                            'options': '-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000'})
    from app.repository import create_repository
    return create_repository(settings).engine


def worker_id() -> str:
    return f'{socket.gethostname()}-{os.getpid()}'[:60]


def run_once(engine, objects, transcriber, assessor, owner: str, now=None) -> str | None:
    """Claim and run one job. Returns its outcome, or None when nothing was ready."""
    with engine.begin() as db:
        job = jobs.claim(db, owner, now=now)
    if job is None:
        return None
    started = time.monotonic()
    outcome = spoken.process(engine, job, objects, transcriber, assessor, now=now)
    # Metrics only: never the transcript, the audio, a learner id or a provider message.
    log.info('job=%s kind=%s attempt=%d/%d outcome=%s seconds=%.1f', job.id, job.kind, job.attempts, job.max_attempts, outcome, time.monotonic() - started)
    return outcome


def run(engine, objects, transcriber, assessor, owner: str | None = None, once: bool = False, poll_seconds: float = 2.0,
        sweep_seconds: float = 60.0, stop: threading.Event | None = None) -> int:
    owner, stop, handled, last_sweep = owner or worker_id(), stop or threading.Event(), 0, 0.0
    while not stop.is_set():
        if time.monotonic() - last_sweep >= sweep_seconds:
            spoken.maintenance(engine, objects)
            last_sweep = time.monotonic()
        outcome = run_once(engine, objects, transcriber, assessor, owner)
        if outcome is not None:
            handled += 1
            continue
        if once:
            break
        stop.wait(poll_seconds)
    return handled


def main(argv: list[str]) -> int:
    from app.config import Settings
    from app.listening.api import prepare
    from app.listening.objects import build_recording_store
    from app.listening.ai_budget import AiGuard
    from app.listening.models import ModelPlan
    from app.listening.providers import build_providers
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings = Settings()
    if not settings.listening_enabled:
        print('AUDLI_LISTENING_ENABLED is off; the listening worker has nothing to do.', file=sys.stderr)
        return 1
    engine = make_engine(settings)
    prepare(engine, worker=True)          # the worker may run under its own least-privilege role (docs/postgres-listening-worker-role.sql)
    objects = build_recording_store(settings, Path(settings.data_dir))      # the worker touches recordings only, never lesson audio
    guard = AiGuard(engine, settings)
    transcriber, assessor = build_providers(settings, guard)
    log.info('AI spending guard: %s', guard.status())
    for model, date in ModelPlan.from_settings(settings).retiring().items():
        log.warning('configured model %s is scheduled for shutdown on %s', model, date)
    stop = threading.Event()
    for name in ('SIGTERM', 'SIGINT'):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    handled = run(engine, objects, transcriber, assessor, once='--once' in argv, stop=stop)
    print(f'listening worker stopped after {handled} job(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
