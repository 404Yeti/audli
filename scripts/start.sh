#!/bin/sh
# Container entry point. The API always starts exactly as before. When Audli 2.0 is enabled and a worker database URL is configured, the
# spoken-answer worker also runs beside it as a separate, restarted process (app/listening/sidecar.py). A failure there never stops the API.
if [ "${AUDLI_LISTENING_ENABLED:-}" = "true" ] && [ -n "${AUDLI_LISTENING_WORKER_DATABASE_URL:-}" ]; then
  python -m app.listening.sidecar &
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
