#!/bin/sh
# Container entry point. The API always starts exactly as before. When Audli 2.0 is enabled and a worker database URL is configured, the
# spoken-answer worker also runs beside it as a separate, restarted process (app/listening/sidecar.py). A failure there never stops the API.
# Same truthy spellings the API's settings accept (true/1/yes/on, any case), so the two can never disagree.
case "$(printf %s "${AUDLI_LISTENING_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on)
    if [ -n "${AUDLI_LISTENING_WORKER_DATABASE_URL:-}" ]; then
      python -m app.listening.sidecar &
    else
      echo "Audli 2.0 is enabled but AUDLI_LISTENING_WORKER_DATABASE_URL is not set; spoken answers will not be processed." >&2
    fi ;;
esac
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
