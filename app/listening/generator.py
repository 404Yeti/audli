"""The lesson-generator process: `python -m app.listening.generator` (run beside the API by app/listening/sidecar.py, never in the API process).

It holds the credentials the spoken-answer worker deliberately lacks (the lesson bucket write key, the speech key) and none that the worker has
(recordings). It claims only 'interpret' and 'generate' jobs. Every learner-facing outcome is written to listening_generation_requests.
"""
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from app.listening import jobs
from app.listening.generation import Deps, FakeInterpreter, FakeScriptWriter, Ledger, CREDIT_MULTIPLIER, voices_from
from app.listening.generation_jobs import MeteredSpeech, process
from app.listening.speech import ElevenLabsSpeech, FakeSpeech, SpeechBudget
from app.listening.worker import make_engine, worker_id

log = logging.getLogger('audli.listening.generator')
KINDS = ('interpret', 'generate')


def run_once(deps: Deps, owner: str, now=None) -> str | None:
    with deps.engine.begin() as db:
        job = jobs.claim(db, owner, now=now, kind=KINDS)
    if job is None:
        return None
    started = time.monotonic()
    outcome = process(deps, job, now=now)
    log.info('job=%s kind=%s attempt=%d/%d outcome=%s seconds=%.1f', job.id, job.kind, job.attempts, job.max_attempts, outcome, time.monotonic() - started)
    return outcome


def run(deps: Deps, owner: str | None = None, once: bool = False, poll_seconds: float = 2.0, stop: threading.Event | None = None) -> int:
    owner, stop, handled = owner or worker_id(), stop or threading.Event(), 0
    while not stop.is_set():
        if run_once(deps, owner) is not None:
            handled += 1
            continue
        if once:
            break
        stop.wait(poll_seconds)
    return handled


def build_deps(settings, engine) -> Deps:
    from app.listening.ai_budget import AiGuard
    from app.listening.objects import build_store
    multiplier = CREDIT_MULTIPLIER[settings.listening_gen_speech_model]
    guard = AiGuard(engine, settings)
    objects = build_store(settings, Path(settings.data_dir))             # the LESSON bucket
    if settings.listening_provider == 'fake':
        if settings.environment == 'production':
            raise RuntimeError('The fake generation providers cannot run in production.')
        return Deps(engine=engine, settings=settings, ledger=Ledger(engine, settings), objects=objects, speech=MeteredSpeech(FakeSpeech(), multiplier),
                    writer=FakeScriptWriter(), interpreter=FakeInterpreter(), voices=voices_from(settings), halted=guard.halted)
    from app.listening.generation import OpenAIInterpreter, OpenAIScriptWriter
    from app.listening.models import ModelPlan
    from app.listening.providers import make_client
    plan = ModelPlan.from_settings(settings).generation
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise RuntimeError('OPENAI_API_KEY is required for the lesson generator.')
    client = make_client(settings.openai_api_key.get_secret_value(), settings.listening_provider_timeout_seconds)
    speech = ElevenLabsSpeech(settings.elevenlabs_api_key.get_secret_value(), model=settings.listening_gen_speech_model, budget=SpeechBudget(1), timeout=60)
    return Deps(engine=engine, settings=settings, ledger=Ledger(engine, settings), objects=objects, speech=MeteredSpeech(speech, multiplier),
                writer=OpenAIScriptWriter(client, plan.model, plan.timeout, plan.max_output_tokens),
                interpreter=OpenAIInterpreter(client, plan.model, plan.timeout),
                voices=voices_from(settings), halted=guard.halted)


def main(argv: list[str]) -> int:
    from app.config import Settings
    from app.listening.api import prepare
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings = Settings()
    if not settings.listening_enabled or not settings.listening_generation_enabled:
        print('Lesson generation is off (AUDLI_LISTENING_ENABLED and AUDLI_LISTENING_GENERATION_ENABLED); the generator has nothing to do.', file=sys.stderr)
        return 1
    engine = make_engine(settings)
    prepare(engine, generator=True)
    deps = build_deps(settings, engine)
    log.info('generation ledger: %s', deps.ledger.status())
    stop = threading.Event()
    for name in ('SIGTERM', 'SIGINT'):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    handled = run(deps, once='--once' in argv, stop=stop)
    print(f'lesson generator stopped after {handled} job(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
