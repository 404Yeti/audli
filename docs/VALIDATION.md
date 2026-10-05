# V0.1 validation report — 2026-10-04

> 2026-10-05 update: the focused live exercise-generation/speech check now passes. See [generation failure and fix](GENERATION_FIX.md). The status below records the earlier validation pass; the full browser loop remains unverified.

**Status: not complete. The real AI/browser end-to-end loop has not been exercised.** No product features or UI redesign were added during this validation pass.

## Results

| Check | Result | Scope |
|---|---|---|
| Python tests | **78 passed** | Domain rules, API gates, persistence, FFmpeg validation, real OpenAI SDK with mocked HTTP |
| Frontend API helper tests | **5 passed** | Actual helper imported by the screen, dependency-free Node execution |
| Python compilation | Passed | app, tests, scripts |
| FastAPI startup | Passed | Uvicorn startup; not proof of reachable HTTP |
| Frontend dependency installation | Blocked | Registry DNS: EAI_AGAIN |
| Next.js production build | Could not start | next unavailable; shell reports Permission denied, exit 127 |
| TypeScript typecheck | Could not start | tsc unavailable; shell reports Permission denied, exit 127 |
| Frontend dev server | Could not start | next unavailable, exit 127 |
| Live localhost HTTP check | Blocked | Sandbox denies socket creation, Operation not permitted |
| Frontend/backend communication | Unverified | Frontend cannot run, localhost client access restricted |
| Lint | Unverified | No lint script/tool configured; build does not establish lint status |
| Live OpenAI smoke test | Blocked | OPENAI_API_KEY not configured |
| Browser playback/recording | Unverified | No working frontend/browser loop |

The tests demonstrate a complete **offline API slice**, not successful real AI or learner interaction. The SDK-backed integration test generates an exercise, sanitizes/serves synthetic audio, accepts a valid synthetic WAV upload, transcribes/evaluates through mocked provider HTTP, commits evaluation and adaptation, and generates a second exercise.

Verified in that integration test:
- Public exercise responses contain only ID, difficulty, audio URL and completed attempt ID.
- Transcript endpoint returns 403 before an attempt and after transcription but before evaluation; returns the original script after evaluation.
- Generated audio tags are stripped; a separate test checks removal of an embedded hidden-script comment.
- Persisted adaptation contains previous score, changed variable, old value, new value and reason.
- An overall score of 1 changes speech_rate from .75 to .80, with exactly one changed primary variable.
- The next stored exercise content, generator input and speech API request all use .80; other difficulty values stay unchanged.
- Evaluation/adaptation survive repository reopening. Duplicate evaluation does not adapt twice.
- Actual structured-output SDK parsing and STT log-probability parsing work against mocked HTTP.
- Generator rejects mismatched difficulty, density and length.

## Bugs fixed

1. Frontend assumed every response was JSON. A proxy HTML error caused a JSON parsing error; a null error body caused a property-access exception. The actual API helper now reports a useful server/input error. Five Node tests cover success, empty current exercise, transcript-gate errors, HTML failures and null error bodies.
2. Recording stopped at the exact backend 120-second cap, leaving no room for timer delay/encoder padding. Auto-stop now triggers at 119 seconds and checks recorder state before stopping. This is a code-review fix; actual browser timing remains unverified.

## Exact commands

From repository root:

```bash
source .venv/bin/activate && pytest -q
node --experimental-strip-types tests/frontend.test.mjs
.venv/bin/python -m compileall -q app tests scripts
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
source .venv/bin/activate && python -m scripts.check_backend_http
```

In `web/`:

```bash
npm install --fetch-retries=0 --cache /tmp/audli-npm-cache
npm run build
npm run typecheck
npm run dev
```

Additional diagnostics: the API-key check used Settings and printed only a configured/not-configured boolean, never the key. `node --experimental-strip-types --test tests/frontend.test.mjs` reported only the test file in this sandbox, so the direct Node command above was used to observe all five individual tests. `node --experimental-strip-types --test --test-isolation=none tests/frontend.test.mjs` was rejected by this Node version; it is not a project command. Early grouped/sandboxed pytest invocations stalled at subprocess/thread wakeups and were interrupted; the standalone command above completed all 78 tests.

Uvicorn was stopped after verification. No real learner audio, transcripts or API keys were added as fixtures; audio fixtures are synthetic tones and evaluator text is original test content.

## Remaining known issues

- No frontend installation, lockfile, build, typecheck, lint or browser runtime result yet.
- No real-provider evaluator accuracy, TTS timing, STT fairness or microphone-format result yet.
- Browser background-tab timer throttling may still produce an overlong recording; backend validation rejects it rather than silently scoring it.
- Approximate generated clip duration and speech-rate requests are not waveform-calibrated.
- Transcription waiting for review is not restored after page reload; record again. Completed feedback/current exercise do resume.
- Demo mode has fixed content/manual transcription/synthetic scoring; it cannot satisfy real V0.1 acceptance.
- A stable 70–85% score or saturated bounds deliberately changes zero variables. The one-variable smoke branch must be tested with a score outside that band and available room to adapt.

## Product-owner manual checklist

1. Configure a private `.env` with `AUDLI_PROVIDER=openai` and your OPENAI_API_KEY. Use an empty/separate data directory for the acceptance session; do not send the key in chat.
2. Install frontend dependencies, run `npm run typecheck` and `npm run build`; start FastAPI and the built frontend (`npm start` inside web). Open http://localhost:3000 and allow the microphone.
3. Enter a name/goal and generate a clip. In browser Network/Elements, confirm no original script, answers, revealing title or transcript appears. GET `/api/exercises/EXERCISE_ID/transcript` must return 403.
4. Play the clip through, record a genuine spoken summary, transcribe it, and correct recognition errors only to match what you said. Check the transcript endpoint still returns 403 before evaluation.
5. Confirm the summary and evaluate. Check normalized backend scores, percentage UI feedback, separate missed/misunderstood information and no grammar grading.
6. Click Show transcript. The transcript endpoint must now return 200 with the original text. Reload and confirm completed feedback persists.
7. Inspect `/api/history` or run `python -m scripts.inspect_attempt ATTEMPT_ID`. Confirm previous_score, changed_variable, old_value, new_value and reason. For an initial score >=85%, expect speech_rate .75→.80 and no other primary change; in-band maintenance is intentionally valid.
8. Continue. Compare new difficulty against the event's new_difficulty. After completing that next attempt, inspect its stored content with the CLI and verify it used the new rate/duration/vocabulary/density. Confirm the new clip starts with its transcript hidden again.
9. Try denied microphone permission/upload fallback, silent/invalid audio, a poor-grammar but correct summary, and uncertainty/retry handling. Verify failed/uncertain attempts do not change progress.

Record successful browser/provider evidence before declaring V0.1 complete. Optional paid evaluator regression: `python -m scripts.evaluate_fixtures` with the virtual environment active.

## Repository structure

Generated `.venv/`, data, caches and protected environment directories are omitted.

```text
audli/
├── AGENTS.md
├── README.md
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── compose.yaml
├── requirements.txt
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── adaptive.py
│   ├── evaluation.py
│   ├── repository.py
│   ├── audio.py
│   ├── security.py
│   └── services/
│       ├── __init__.py
│       ├── provider.py
│       ├── openai_provider.py
│       └── demo.py
├── web/
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.ts
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   └── globals.css
│   └── lib/
│       └── api.ts
├── tests/
│   ├── conftest.py
│   ├── test_adaptive.py
│   ├── test_api.py
│   ├── test_audio.py
│   ├── test_evaluation.py
│   ├── test_openai_provider.py
│   ├── frontend.test.mjs
│   └── fixtures/
│       └── evaluator_cases.json
├── scripts/
│   ├── check_backend_http.py
│   ├── evaluate_fixtures.py
│   └── inspect_attempt.py
└── docs/
    ├── PLAN.md
    ├── VALIDATION.md
    └── postgres.sql
```
