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
    fi
    # The lesson generator is a separate process with its own database role and its own credentials; it starts only when generation is switched on.
    case "$(printf %s "${AUDLI_LISTENING_GENERATION_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in
      true|1|yes|on)
        if [ -n "${AUDLI_LISTENING_GENERATOR_DATABASE_URL:-}" ]; then
          python -m app.listening.sidecar generator &
        else
          echo "Lesson generation is enabled but AUDLI_LISTENING_GENERATOR_DATABASE_URL is not set; lessons will not be generated." >&2
        fi ;;
    esac
    # The Daily Challenge scheduler: a third separate process with its own role (docs/LISTENING-DAILY-CHALLENGE-IMPL.md). It needs generation on, because the generator builds the lessons.
    case "$(printf %s "${AUDLI_LISTENING_DAILY_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in
      true|1|yes|on)
        if [ -n "${AUDLI_LISTENING_DAILY_DATABASE_URL:-}" ]; then
          python -m app.listening.sidecar daily &
        else
          echo "The daily challenge is enabled but AUDLI_LISTENING_DAILY_DATABASE_URL is not set; no challenge will be generated or published." >&2
        fi ;;
    esac ;;
esac
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
