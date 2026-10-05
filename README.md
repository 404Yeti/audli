# Audli — Train your ears.

Audli V0.1 is an adaptive English listening-comprehension prototype. The loop is **listen → spoken summary → transcription → comprehension feedback → one-variable adaptation → another clip**. It grades understanding, not speaking grammar.

## Run locally

Requirements: Python 3.12+ and Node.js 20.9+. Commands run from the repository root unless noted.

```bash
cp .env.example .env
# Set OPENAI_API_KEY in .env. Leave AUDLI_PROVIDER=openai for actual AI evaluation.
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd web
npm install
npm run dev
```

Open **http://localhost:3000** and allow microphone access. Native Windows: use `.venv\Scripts\python.exe -m pip` / `-m uvicorn` instead of `.venv/bin/...`.

The browser talks through Next.js to the backend; no OpenAI credentials go to the client. `.env` is loaded by backend settings. Generated exercise audio and SQLite progress live under `data/`, which is ignored by Git. FFmpeg is bundled through `imageio-ffmpeg` for audio validation; you can override its path with `IMAGEIO_FFMPEG_EXE`.

### Docker alternative

```bash
cp .env.example .env
# Set your API key, or choose demo mode.
docker compose up --build
```

Open http://localhost:3000. Both published ports bind to loopback. Docker Desktop on Windows needs WSL integration enabled if running these commands in WSL. Run a **single API worker** for this local learner.

### Explicit demo mode

Set `AUDLI_PROVIDER=demo` and use a separate `AUDLI_DATA_DIR=data/demo` if switching modes. Docker includes `espeak`; native demo requires it installed on your machine (e.g. `sudo apt-get install espeak`). Demo reads an original sample using synthetic speech, accepts a real recording, asks you to type what you said, and returns **fixed partial scores**. It does not perform STT or measure comprehension, and its fixed script does not implement content-level adaptation. Use OpenAI mode to validate the product hypothesis.

## Use the loop

1. Enter a name and listening goal. Typed onboarding keeps V0.1 small.
2. Generate a clip and play it through. The browser receives no transcript, revealing title, questions or expected answers.
3. Tap to start/stop recording (2–120 seconds). Existing spoken recordings can also be uploaded.
4. Transcribe, then check/correct recognition errors to match what you actually said. Confirm before evaluation. Poor grammar is acceptable.
5. Review scores, understood/missed/misunderstood units and feedback. Show transcript is available only after a successful evaluation.
6. Continue. The next clip uses the persisted new difficulty. The first three successful attempts establish a simple baseline; later scores use exponential smoothing.

If generation/transcription/evaluation fails, retry the step. Existing progress is preserved. Reload resumes the current clip or completed feedback. A transcription awaiting review is not resumed after reload: record again. Unreliable evaluator transcription flags produce no score or adaptation. Unknown/low STT confidence prompts review; learner confirmation is an explicit manual resolution, not a claim that provider confidence improved.

## Architecture and rubric

- `app/models.py`: strict structured schemas and bounds.
- `app/services/provider.py`: replaceable generation, speech, transcription and evaluation interface; OpenAI SDK lives only in its implementation.
- `app/evaluation.py`: scores every expected information unit, rejecting missing/duplicate judgments and cited evidence absent from the learner transcript. Understood = 1, partial = .5, missed/misunderstood = 0. Category scores are means. Overall weights: main idea .35, details .40, vocabulary .15, inference .10. Vocabulary means contextual meaning, never exact wording.
- `app/adaptive.py`: pure deterministic policy. At/above edge high: increase slightly; in edge band: keep difficulty and focus the weakest category; below edge low: reduce one variable, more clearly below low-score threshold. Bounds can cause no change. Each event records thresholds, previous score, harder/same/easier, old/new values, full old/new difficulty, weakest dimension and reason.
- `app/repository.py`: SQLite transactions persist users, profiles, exercises, attempts, evaluation results and adaptation events. A unique index prevents multiple evaluations of the same exercise. `docs/postgres.sql` is a reference for a future PostgreSQL/Supabase repository, not a configured integration.
- `web/app/page.tsx`: responsive audio-first flow using MediaRecorder; `next.config.ts` proxies to FastAPI.

Adaptation changes **at most one** primary variable. Within the target band it deliberately changes none; otherwise exactly one changes unless all variables are saturated. Future speaker/accent/noise fields are fixed and cannot adapt. Natural-speed ability stays unknown because slow-clip scores cannot establish it.

Transcription confidence is the exponential mean token log probability when supplied, a heuristic rather than a calibrated measure. Always let the learner review their own recognized text. Recordings are validated by MIME, signature, actual decoding, duration, size and silence detection. Generated audio is re-encoded with metadata stripped so embedded tags cannot reveal the script. Temporary learner audio is deleted immediately after validation and is never persisted; recognized and corrected text is persisted. Audio is sent to OpenAI in real mode. Generated exercise audio stays local. No transcript/debug HTTP route bypasses the completion gate.

## Verify

```bash
.venv/bin/pytest -q
node --experimental-strip-types tests/frontend.test.mjs
cd web && npm run typecheck && npm run build
```

The Node check uses Node 22.18+ (or a compatible version with TypeScript stripping) and exercises the actual client API helper without installing frontend dependencies. It does not typecheck the screen or run Next.js.

Tests cover adaptive score boundaries, ranges, one-variable changes, initial profile updates, strict schemas, information-unit coverage, uncertainty/retry behavior, API transcript privacy, persistence and duplicate evaluations. Evaluator fixture tests verify application scoring with prescribed judgments, **not actual model quality**.

For a paid, real-provider evaluator regression after configuring your API key:

```bash
.venv/bin/python -m scripts.evaluate_fixtures
```

This checks excellent, details missed, misunderstanding, poor, and poor-grammar/correct-meaning responses against expected score ranges. Inspect a completed attempt locally:

```bash
.venv/bin/python -m scripts.inspect_attempt ATTEMPT_ID
```

Get evaluated attempt IDs from `/api/history`. The CLI refuses unevaluated attempts; its output contains sensitive text and should stay local. There is no teacher dashboard. Adaptation events include a nullable teacher decision for future agreement-rate measurement.

### Real-provider acceptance check

With OpenAI mode and a key, complete two clips in a browser: verify audio playback, hidden transcript (also 403 when requesting its endpoint), recording/transcription correction, structured scores/feedback, persisted adaptation and next-clip difficulty. Repeat with imperfect grammar. Try denying microphone access and using the upload fallback. Test Chromium and Safari on localhost/HTTPS before learner sessions. This is required before claiming live end-to-end validation.

## Development error diagnostics

Set `AUDLI_ENVIRONMENT=development` (the local default) for operation names, exception types, sanitized messages and traceback frames. Logs omit locals, source snippets, Pydantic input values, authorization values, credentials and binary payloads. Set `AUDLI_ENVIRONMENT=production` for operation/type-only logs. Learner-facing errors remain generic.

To explicitly verify real exercise generation and speech using paid API calls without changing the local learner session:

```bash
source .venv/bin/activate
pytest -q tests/live_generation.py -s
```

The live check uses an isolated temporary database and is excluded from normal test discovery. See [the diagnosed generation failure and fix](docs/GENERATION_FIX.md).

## Limits of V0.1

Single local/test learner, no authentication or public deployment. Loopback-only use protects the local state; browser write requests from non-local origins are rejected. Hosted/mobile-device access needs explicit access controls and HTTPS. Keep `data/` private; deleting it after stopping the server starts a fresh local profile. Do not reuse real learner data in demo mode.

There is no verbal goal extraction, teacher dashboard, payments, pronunciation grading, external media or complex assessment framework. Clip duration is approximate; speed is requested numerically, not inferred from the waveform. LLM comprehension quality and STT fairness still need human/teacher validation; fixture regressions are an initial check, not proof of learning efficacy.

Implementation follows the official [structured-output](https://developers.openai.com/api/docs/guides/structured-outputs), [transcription](https://developers.openai.com/api/docs/guides/speech-to-text) and [speech-generation](https://developers.openai.com/api/docs/guides/text-to-speech) documentation. See [validation status](docs/VALIDATION.md), [engineering rules](AGENTS.md) and [implementation plan and risks](docs/PLAN.md).
