"""Run the spoken-answer worker beside the API in the same container, as a SEPARATE process with its own database role.

For a host that has one always-on service and no separate background-worker plan. Nothing about the worker changes: it is still
`python -m app.listening.worker`, still talks to PostgreSQL only through the restricted `audli_listening_worker` role, and still holds
the recordings bucket and no lesson-audio credentials. What this module adds is the launch: it builds the worker's environment from an
explicit allow-list (never a copy of the API's), starts the worker, and restarts it with a growing pause if it exits. If the worker
crashes the API keeps serving; if the container restarts, unfinished jobs are picked up again from their leases.

Enabled only when AUDLI_LISTENING_ENABLED=true and AUDLI_LISTENING_WORKER_DATABASE_URL is set (scripts/start.sh).
"""
import os
import signal
import subprocess
import sys
import time

# Variables the worker may inherit. AUDLI_DATABASE_URL is deliberately absent: it is replaced by the worker role's URL.
# Lesson-audio credentials (AUDLI_LISTENING_S3_*), the signing secret and the ops token are never passed.
PASS_THROUGH = (
    'PATH', 'LANG', 'PYTHONUNBUFFERED', 'AUDLI_ENVIRONMENT', 'AUDLI_PERSISTENCE', 'AUDLI_AUTH_MODE', 'AUDLI_SUPABASE_URL',
    'AUDLI_SUPABASE_PUBLISHABLE_KEY', 'AUDLI_LISTENING_ENABLED', 'AUDLI_LISTENING_PROVIDER', 'OPENAI_API_KEY', 'AUDLI_LISTENING_STORAGE',
    'AUDLI_LISTENING_RECORDINGS_ENDPOINT', 'AUDLI_LISTENING_RECORDINGS_BUCKET', 'AUDLI_LISTENING_RECORDINGS_ACCESS_KEY',
    'AUDLI_LISTENING_RECORDINGS_SECRET_KEY', 'AUDLI_LISTENING_S3_REGION', 'AUDLI_LISTENING_TRANSCRIPTION_MODEL', 'AUDLI_LISTENING_ASSESSMENT_MODEL',
    'AUDLI_LISTENING_ASSESSMENT_API', 'AUDLI_LISTENING_ASSESSMENT_REASONING', 'AUDLI_LISTENING_GENERATION_MODEL', 'AUDLI_LISTENING_AI_ENABLED',
    'AUDLI_LISTENING_AI_DAILY_BUDGET_USD', 'AUDLI_LISTENING_PROVIDER_TIMEOUT_SECONDS', 'AUDLI_DATA_DIR')
WORKER_URL = 'AUDLI_LISTENING_WORKER_DATABASE_URL'
# The lesson GENERATOR (Milestone 05) is a second, separate process with its own role and its own allow-list: it gets the lesson bucket write key and the
# speech key, and never the recordings credentials, the signing secret or the ops token. The spoken-answer worker above never gets either of the first two.
GENERATOR_URL = 'AUDLI_LISTENING_GENERATOR_DATABASE_URL'
GENERATOR_PASS_THROUGH = (
    'PATH', 'LANG', 'PYTHONUNBUFFERED', 'AUDLI_ENVIRONMENT', 'AUDLI_PERSISTENCE', 'AUDLI_AUTH_MODE', 'AUDLI_SUPABASE_URL', 'AUDLI_SUPABASE_PUBLISHABLE_KEY',
    'AUDLI_LISTENING_ENABLED', 'AUDLI_LISTENING_GENERATION_ENABLED', 'AUDLI_LISTENING_PROVIDER', 'OPENAI_API_KEY', 'ELEVENLABS_API_KEY',
    'AUDLI_LISTENING_STORAGE', 'AUDLI_LISTENING_S3_ENDPOINT', 'AUDLI_LISTENING_S3_BUCKET', 'AUDLI_LISTENING_S3_REGION', 'AUDLI_LISTENING_S3_ACCESS_KEY',
    'AUDLI_LISTENING_S3_SECRET_KEY', 'AUDLI_LISTENING_GENERATION_MODEL', 'AUDLI_LISTENING_AI_ENABLED', 'AUDLI_LISTENING_PROVIDER_TIMEOUT_SECONDS',
    'AUDLI_LISTENING_GEN_SPEECH_MODEL', 'AUDLI_LISTENING_GEN_MAX_MINUTES', 'AUDLI_LISTENING_GEN_DAILY_BUDGET_USD', 'AUDLI_LISTENING_GEN_MONTHLY_CREDITS',
    'AUDLI_LISTENING_GEN_PER_LEARNER_DAILY', 'AUDLI_LISTENING_GEN_USD_PER_1K_CREDITS', 'AUDLI_LISTENING_GEN_VOICES', 'AUDLI_DATA_DIR',
    # The generator re-checks the accent/format the same way the API does (spec_problem), so it must see the same two switches or it fails every non-original accent.
    'AUDLI_LISTENING_FORMATS_ENABLED', 'AUDLI_LISTENING_FORMAT_ACCENTS')
# The DAILY CHALLENGE scheduler (third process, own role): it publishes through the curator workflow, so it alone holds the DELETE-on-owners grant. It receives the
# AI key (semantic review, topic proposals) but neither the speech key, nor any bucket credential, nor the recordings credentials, nor the signing secret.
DAILY_URL = 'AUDLI_LISTENING_DAILY_DATABASE_URL'
DAILY_PASS_THROUGH = (
    'PATH', 'LANG', 'PYTHONUNBUFFERED', 'AUDLI_ENVIRONMENT', 'AUDLI_PERSISTENCE', 'AUDLI_AUTH_MODE', 'AUDLI_SUPABASE_URL', 'AUDLI_SUPABASE_PUBLISHABLE_KEY',
    'AUDLI_LISTENING_ENABLED', 'AUDLI_LISTENING_GENERATION_ENABLED', 'AUDLI_LISTENING_PROVIDER', 'OPENAI_API_KEY', 'AUDLI_LISTENING_GENERATION_MODEL',
    'AUDLI_LISTENING_STORAGE',       # a setting, not a credential: production validation insists lessons use object storage, the scheduler just never touches it
    'AUDLI_LISTENING_AI_ENABLED', 'AUDLI_LISTENING_AI_DAILY_BUDGET_USD', 'AUDLI_LISTENING_PROVIDER_TIMEOUT_SECONDS', 'AUDLI_LISTENING_GEN_SPEECH_MODEL',
    'AUDLI_LISTENING_GEN_MAX_MINUTES', 'AUDLI_LISTENING_GEN_DAILY_BUDGET_USD', 'AUDLI_LISTENING_GEN_MONTHLY_CREDITS', 'AUDLI_LISTENING_GEN_PER_LEARNER_DAILY',
    'AUDLI_LISTENING_GEN_CURATOR_DAILY', 'AUDLI_LISTENING_GEN_USD_PER_1K_CREDITS', 'AUDLI_LISTENING_GEN_VOICES', 'AUDLI_LISTENING_FORMATS_ENABLED',
    'AUDLI_LISTENING_FORMAT_ACCENTS', 'AUDLI_LISTENING_CURATOR_IDS', 'AUDLI_DATA_DIR', 'AUDLI_LISTENING_DAILY_ENABLED', 'AUDLI_LISTENING_DAILY_PUBLISH',
    'AUDLI_LISTENING_DAILY_SERVICE_ID', 'AUDLI_LISTENING_DAILY_TIMEZONE', 'AUDLI_LISTENING_DAILY_GENERATE_HOUR', 'AUDLI_LISTENING_DAILY_MAX_ATTEMPTS',
    'AUDLI_LISTENING_DAILY_MAX_USD', 'AUDLI_LISTENING_DAILY_REVIEW_MAX_USD', 'AUDLI_LISTENING_DAILY_REVIEW_MODEL', 'AUDLI_LISTENING_DAILY_MINUTES',
    'AUDLI_LISTENING_DAILY_LEVELS', 'AUDLI_LISTENING_DAILY_POOL_LOW', 'AUDLI_LISTENING_DAILY_ALERT_WEBHOOK')
ROLES = {'worker': ('app.listening.worker', WORKER_URL, PASS_THROUGH, 'worker'),
         'generator': ('app.listening.generator', GENERATOR_URL, GENERATOR_PASS_THROUGH, 'generator'),
         'daily': ('app.listening.daily', DAILY_URL, DAILY_PASS_THROUGH, 'daily')}
ARGS = {'daily': ('run',)}
MIN_PAUSE, MAX_PAUSE, HEALTHY_AFTER = 5.0, 120.0, 60.0


def worker_environment(source, role: str = 'worker') -> dict[str, str]:
    _, url_var, allowed, process = ROLES[role]
    env = {key: source[key] for key in allowed if source.get(key)}
    env['AUDLI_DATABASE_URL'] = source[url_var]
    env['AUDLI_LISTENING_PROCESS'] = process
    env.setdefault('PYTHONUNBUFFERED', '1')
    return env


def next_pause(previous: float, ran_seconds: float) -> float:
    """A worker that ran for a while before exiting restarts quickly; one that dies at once backs off (never hammering the database)."""
    return MIN_PAUSE if ran_seconds >= HEALTHY_AFTER else min(MAX_PAUSE, max(MIN_PAUSE, previous * 2))


def main(source=None, run=subprocess.run, sleep=time.sleep, clock=time.monotonic, rounds: int | None = None, role: str = 'worker') -> int:
    source = os.environ if source is None else source
    module, url_var, _, _ = ROLES[role]
    if not source.get(url_var):
        print(f'{url_var} is not set; no {role} to run.', file=sys.stderr)
        return 1
    env, pause, stopping, count = worker_environment(source, role), MIN_PAUSE, [False], 0
    signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__(0, True))
    while not stopping[0] and (rounds is None or count < rounds):
        count += 1
        started = clock()
        result = run([sys.executable, '-m', module, *ARGS.get(role, ())], env=env, check=False)
        pause = next_pause(pause, clock() - started)
        print(f'listening {role} exited with status {result.returncode}; restarting in {pause:.0f}s', file=sys.stderr, flush=True)
        sleep(pause)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(role=sys.argv[1] if len(sys.argv) > 1 else 'worker'))
