"""Isolated acceptance server for the 2.0 listening lesson: real routes, real SQLite, the real seed lesson and its audio.

Only launched by web/listening.playwright.config.ts. It also starts the real, separate worker process (python -m app.listening.worker)
against the same database and object folder, with the deterministic fake AI providers: no key, no network call, no paid request, and
the old conversational engine is never called.
"""
import atexit
import os
import pathlib
import subprocess
import sys
import tempfile

_workspace = tempfile.TemporaryDirectory(prefix='audli-listening-')
os.environ.update(AUDLI_ENVIRONMENT='development', AUDLI_PERSISTENCE='sqlite', AUDLI_DATABASE_URL='', AUDLI_LEARNER_ID='',
                  AUDLI_AUTH_MODE='local', AUDLI_DATA_DIR=_workspace.name, AUDLI_SUPABASE_URL='', AUDLI_SUPABASE_PUBLISHABLE_KEY='',
                  AUDLI_LISTENING_ENABLED='true', AUDLI_PROVIDER='demo', AUDLI_LISTENING_PROVIDER='fake',
                  AUDLI_LISTENING_FAKE_DELAY_SECONDS='6',
                  AUDLI_LISTENING_FAKE_TRANSCRIPT='I think the restaurant had run out of mushrooms, so Maria chose the chicken without onions instead')

from app.config import Settings
from app.listening.objects import build_store
from app.listening.seed import seed
from app.main import create_app

_settings = Settings(_env_file=None)
app = create_app(_settings)
seed(app.state.repository.engine, build_store(_settings, _settings.data_dir))


# The worker is its own process, started after the lesson is seeded so both share one database and one set of files.
_worker = subprocess.Popen([sys.executable, '-m', 'app.listening.worker'], env=os.environ.copy(), cwd=pathlib.Path(__file__).resolve().parents[1])
atexit.register(_worker.terminate)
