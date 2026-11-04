"""Isolated acceptance server for the 2.0 listening lesson: real routes, real SQLite, the real seed lesson and its audio.

Only launched by web/listening.playwright.config.ts. No provider, microphone, key or production data is involved, and the old
conversational engine is never called (no provider is configured at all).
"""
import os
import tempfile

_workspace = tempfile.TemporaryDirectory(prefix='audli-listening-')
os.environ.update(AUDLI_ENVIRONMENT='development', AUDLI_PERSISTENCE='sqlite', AUDLI_DATABASE_URL='', AUDLI_LEARNER_ID='',
                  AUDLI_AUTH_MODE='local', AUDLI_DATA_DIR=_workspace.name, AUDLI_SUPABASE_URL='', AUDLI_SUPABASE_PUBLISHABLE_KEY='',
                  AUDLI_LISTENING_ENABLED='true', AUDLI_PROVIDER='demo')

from app.config import Settings
from app.listening.objects import build_store
from app.listening.seed import seed
from app.main import create_app

_settings = Settings(_env_file=None)
app = create_app(_settings)
seed(app.state.repository.engine, build_store(_settings, _settings.data_dir))
