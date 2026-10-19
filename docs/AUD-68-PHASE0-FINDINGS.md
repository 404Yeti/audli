# AUD-68 Phase 0 — diagnostics findings

Linear: AUD-68. Base: `origin/main` @ `289e093`. Scope: diagnostics only; **no behaviour change**, nothing deployed.
Companion: [architecture proposal](ARCHITECTURE-FOUR-PAGE-REALTIME-INVESTIGATION.md).

Legend — **[M]** measured by this PR in a controlled local harness · **[C]** cited from earlier repo documents (not re-measured) ·
**[E]** estimate/hypothesis · **[?]** unknown, needs data.

## 1. Headline results

| # | Finding | Status |
|---|---|---|
| 1 | The process-wide `asyncio.Lock()` **serializes provider-bound work across different learners**. Two simultaneous uploads take **1.94–2.00×** one upload (6 runs, 2 delays). | **[M]** |
| 2 | While learner A's STT runs, learner B's *cheap* lock-taking request waits for it (**0.27 s of a 0.30 s STT; 0.83 s of 1.0 s**); a route without the lock answers in **3–4 ms**. | **[M]** |
| 3 | Application code (validation, persistence, adaptation, ffmpeg sanitization, cache I/O) costs **≈100–130 ms per full turn** with zero-delay providers. Provider time dominates. | **[M]** (harness) |
| 4 | Production-safe numeric telemetry is implemented, off by default, and verified not to emit IDs, text, tokens or concrete paths. | **[M]** (tests) |
| 5 | End-to-end turn latency in production is **still unknown**: no p50/p90 exists. Earlier dev-mode samples: 9.8, 10.3, 12.1, 43.2 s. | **[C]** / **[?]** |
| 6 | Vietnamese-accent STT accuracy is **unknown**. Harness, prompt set (34 prompts) and consent protocol are ready; **no audio has been evaluated.** | **[?]** |
| 7 | Short replies: the transcriber flags replies under 3 words and the general gate rejects any uncertainty, so correct 1–2-word answers fail. Real-provider run: 6/10 short replies rejected as `too_short`; non-speech clips produced no text (n=4). Homework already uses a closed-intent exception. Context-aware proposal: [AUD-68-SHORT-REPLY-RULE.md](AUD-68-SHORT-REPLY-RULE.md). | **[M]** small n |
| 8 | Real-provider baseline (synthetic): STT p50 0.73 s, assessment p50 2.4 s, OpenAI feedback TTS p50 2.35 s. See §4b. | **[M]** small n |

## 2. The shared lock (task 2)

### What the code does
`app/main.py:46` creates one `asyncio.Lock()` per process. It is passed to every route group (`conversation_api`, `lesson_api`, `homework_api`, `onboarding_api`, `planner_api`, …). Provider calls run **inside** it: STT in `turn.upload` / `lesson.upload` / `onboarding.upload`, LLM assessment in `turn.assessment`, check-in and extraction in `lesson.answer`, TTS in `turn.coach_audio`. Production runs `uvicorn --workers 1` (`Dockerfile`). `docs/AUD-21-LATENCY.md` says "per-learner locks"; **no per-learner lock exists** in `app/` (only `acknowledgment_lock`, `retry_lock`, `preview_lock`, which are likewise process-wide).

### Controlled test (`tests/lock_concurrency.py`, `scripts/measure_lock_concurrency.py`)
Two real authenticated scopes (fake Supabase auth), one app, fake STT with a fixed delay, in-process ASGI, SQLite. [M]

| STT delay | solo upload | A+B simultaneously | ratio | B `GET /profile` (no lock) | B lock-taking route behind A |
|---|---|---|---|---|---|
| 0.3 s | 0.33–0.34 s | 0.655–0.660 s | 1.94–1.99 | 3–4 ms | 0.269–0.272 s |
| 1.0 s | 1.03–1.04 s | 2.06 s | 1.99–2.00 | 3–4 ms | 0.832–0.833 s |

Raw: `docs/AUD-68-LOCK-CONCURRENCY.json`. Guarded by `tests/test_lock_concurrency.py` (characterization: documents current behaviour; invert it when per-learner locking lands).

### Interpretation (and its limits)
* With *N* concurrent learners, the *N*th waits roughly *(N−1) × provider time* behind the others on the same lock. Real STT is ~1.3–2.6 s and assessment ~3.4–5.5 s **[C]**, so two learners starting an answer together could add several seconds for one of them. Only one active learner is needed to see no effect.
* This is **independent of any Realtime/WebRTC decision**: it affects the existing pipeline today.
* Not measured: real concurrent usage (no production concurrency data), Postgres contention, or SDK retries.
* Safety note for the fix (Phase 4, not done here): the lock also orders per-learner writes. A per-learner lock must keep that ordering; cross-learner state is protected by the repository's own CAS/transactions, but this needs its own review and tests.

## 3. Instrumentation (task 1)

* `app/telemetry.py`: bounded in-memory aggregator — fixed histogram buckets (25 ms … 60 s), per-stage count/errors/mean/max and **upper-bound** p50/p90/p99, lock queue depth at arrival. Stage names are code constants or route *templates* (`http.POST /api/lessons/{lesson_id}/heard`); anything else collapses to `other`; ≤160 stages.
* `app/diagnostics.py`: `operation()` feeds the aggregator; `timed_lock()` now also records **`<name>.lock_hold`** and queue depth (previously only `lock_wait`).
* `app/main.py`: per-route request timing; the window (default 300 s, flushed at shutdown) is logged as a few small `Latency summary {json}` lines (≤24 stages each, with `window`/`part`/`parts`), so no single line approaches a log-pipeline limit (worst case with every route populated would otherwise be ≈28 KB).
* `POST /api/telemetry/turn`: browser timings, **authenticated like every `/api` route**, strict schema (fixed stage enum, integers 0–300000, no extra keys), never touches learner state or the shared lock.
* Web: `createTurnReporter` batches stage timings and sends one report per meaningful turn; build-time opt-in `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1`; failures ignored.
* Off by default: `AUDLI_LATENCY_AGGREGATES=false`. Existing `AUDLI_LATENCY_TELEMETRY` (Server-Timing headers) unchanged.

Privacy verification (`tests/test_telemetry.py`): the emitted line contains none of the exercise ID, attempt ID, transcript text, bearer token or user UUID; invalid/long/free-text stage names never become keys; the report endpoint rejects free text, unknown stages, floats, negatives and extra keys. No audio, transcript, learner ID or per-request record is retained; the aggregate is per-process and resets each window.

Expected cost: ~one dict update per stage per request; log volume ≈ 2–7 lines of ≲5 KB per 5 min when active; nothing when idle.

## 4. Baselines (task 3)

| Component | Value | Source |
|---|---|---|
| Application overhead per turn (upload validation ≈19 ms, STT bookkeeping ≈0, assessment+persist+adapt ≈24 ms total request, coach-audio incl. ffmpeg sanitization ≈35 ms, ready ≈13 ms) | **≈100–130 ms** | **[M]** `docs/AUD-68-APP-OVERHEAD.json` (20 turns; histogram upper bounds, means shown in file) |
| Silence detection | 1.8 s fixed | code (`hands-free.ts`) |
| STT request | 1.3–2.6 s (one 33.8 s outlier, cause unproven) | **[C]** AUD-21, dev mode, n=4 |
| Assessment request | 3.4–5.5 s | **[C]** AUD-21, dev mode, n=4 |
| Coach TTS request (old OpenAI voice) | 2.0–3.1 s | **[C]** AUD-21 |
| ElevenLabs tutor TTS, production | first byte median 139 ms; validated readiness median 797 ms (max 1.6 s) | **[C]** AUD-21, production logs, n=11 |
| Browser end-to-end (last voice → `playing`) | 9.8 / 10.3 / 12.1 / 43.2 s | **[C]** AUD-21, dev mode, n=4 |
| **Production p50/p90 for any stage** | — | **[?]** |

Not obtained, and why:
1. **Production logs.** The Render connector has no workspace selected; awaiting the owner to identify the correct one. Existing `Tutor TTS` log lines would refresh the TTS numbers above.
2. **Production-voice benchmark.** Local env has no ElevenLabs key (see §4b).
3. **Production end-to-end.** Requires deploying this telemetry with the two flags on (owner decision).

Interpretation [E]: if the earlier samples are representative, assessment (~35–45%) and the sum of STT+TTS+silence (~40–50%) dominate; our own code is ~1%. Do not treat this split as measured production truth.

### 4b. Controlled real-provider benchmark (owner-authorized; added after review)
`scripts/benchmark_providers.py` → `docs/AUD-68-PROVIDER-BASELINE.json`. Synthetic content only; local env key read by variable *name* allowlist
(no value printed, no database/Supabase/production account); 24 STT + 26 TTS + 1 generation + 6 assessment calls per run (estimated spend: cents;
two runs). Models as configured: `gpt-4.1-mini`, `gpt-4o-mini-transcribe`, `gpt-4o-mini-tts` / `coral`. **No ElevenLabs key is present locally, so the
ElevenLabs tutor voice was not benchmarked.** Small n; US-English synthetic voice, **not** accented learners. [M]

| Stage (provider-only, run 2) | n | p50 | p90 |
|---|---|---|---|
| STT (`gpt-4o-mini-transcribe`, 3–6 s clips) | 24 | 0.73 s | 1.06 s |
| Evidence assessment (`gpt-4.1-mini`, structured) | 6 | 2.40 s | 2.88 s |
| Feedback TTS (OpenAI `coral`, ≈15 words) | 6 | 2.35 s | 3.09 s |
| ffmpeg sanitization of that audio | 6 | 0.12 s | 0.33 s |
| Provider-bound composite at stage p50s | — | **≈5.1–5.6 s** | — |

Add the 1.8 s silence window, upload/network, app overhead (≈0.1 s) and playback start for a full turn: roughly **7.5–9 s** at p50 with the OpenAI
voice, consistent with earlier dev-mode samples (9.8–12.1 s) given their older, slower TTS. Production uses ElevenLabs for the tutor voice
(earlier production logs: validated audio readiness median ≈0.8 s), which would lower the TTS term. That is an [E], not a measurement.
Runs differed slightly (assessment p50 2.6 s vs 2.4 s; TTS 1.7 s vs 2.35 s): TTS and LLM latency varies by a second or more run to run.

## 5. Vietnamese-accent STT evaluation (task 4)

Ready, not run:
* `scripts/evaluate_stt.py` — runs the **production** `OpenAIProvider.transcribe` (or a fake for plumbing) over a manifest; reports WER, confidence, `reliable_recognition` pass rate, false-reliable (accepted at WER > 0.25) and false-unreliable (rejected at WER ≤ 0.10) rates, a threshold sweep (0.50–0.90), uncertainty-reason counts and STT latency, by category / speaker / provenance. Sequential calls so latency has no self-inflicted queueing. Reports are numbers only; transcripts require `--include-text`.
* `tests/fixtures/stt_eval/prompts.vi-en.json` — 34 prompts: 12 read-aloud (Vietnamese-ESL phonology: final-stop/cluster reduction, /θ ð/, /ʃ s/, /r l/, vowel length, -ed/-s endings), 8 learner-style answers with **poor grammar and correct meaning** (AGENTS.md requirement), 10 short replies, 4 number/name sentences.
* Guards: only `consented` (requires a consent record) or `synthetic` (requires a named source) samples; audio must live inside the manifest directory (outside the repo); size limited to the production limit. Learner production audio is refused by design.
* Tests: `tests/test_stt_eval.py` (WER, manifest safety, numbers-only report). These prove plumbing, not accuracy (AGENTS.md: mock tests establish plumbing).

Protocol (summary; owner/legal review needed before any collection):
1. **Consent** — written, specific to this evaluation, covers recording, storage location, retention (delete on a stated date), no use for training, withdrawal; adults only; pseudonymous speaker IDs; no names in filenames.
2. **Sample** — ≥20 speakers × the 34 prompts, mixed levels (A2–C1), mixed devices (iPhone Safari primary), quiet and moderate-noise rooms. Smaller pilots (e.g. 5 speakers) can show gross problems but **cannot** support a go/no-go.
3. **Synthetic option** — TTS-generated "Vietnamese-accented" speech is cheap to produce but is **not** evidence about real learners; use it only for harness validation, never to set thresholds. It also spends provider credits and was not generated here.
4. **Decision rule to agree before running** — e.g. false-reliable rate ≤ 2% at the chosen threshold; short-reply (`S`) pass rate reported separately; no category with gate pass rate far below others.
5. **Gate relevance** — any alternative transcription path (e.g. streaming) must be evaluated with this same harness, and must supply a confidence signal or an equivalent safeguard. The documented `gpt-live-transcribe` model returns **no confidence**, which `reliable_recognition` treats as unreliable.

## 6. What Phase 0 does not change
Adaptive logic, evaluator, ElevenLabs/Coral voice and fallback, transcript gates, confidence gate, Rive assets, lesson flow, schemas and API contracts for existing clients. One additive route (`/api/telemetry/turn`) and three additive settings (`AUDLI_LATENCY_AGGREGATES`, `AUDLI_LATENCY_SUMMARY_SECONDS`, build flag `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING`).

## 7. Decisions needed from the owner
1. **Per-learner locking** (Phase 4 in the proposal): finding 1–2 is measurable today. Authorize a separate scoped change? It alters serialization semantics, so I will not fold it into Phase 1.
2. **Enable telemetry in production** (`AUDLI_LATENCY_AGGREGATES=true`; web build flag) to get real p50/p90. Needs a deploy, which I will not do.
3. **Render workspace** — confirm which workspace I may read logs from (read-only) to refresh production TTS numbers.
4. **Consented accent audio** — the real-provider benchmark is done (§4b, synthetic). Accent accuracy still needs consented Vietnamese-accent recordings.
5. **Short-reply rule** — approve Step A (pure refactor + counters) of [AUD-68-SHORT-REPLY-RULE.md](AUD-68-SHORT-REPLY-RULE.md)? Step B (behaviour change) awaits accent/noise data.
6. **Linear numbering (resolved)** — AUD-68 = Phase 0, AUD-69 = Phase 1. The earlier lifecycle work (PR #50, `docs/AUD-68-LESSON-LIFECYCLE.md`) carries the same number in history; it is left unchanged and new documents cite "AUD-68 Phase 0" or the PR number.

## 8. Reproduce
```sh
.venv/bin/pytest -q tests/test_lock_concurrency.py tests/test_telemetry.py tests/test_stt_eval.py
.venv/bin/python scripts/measure_lock_concurrency.py --delays 0.3 1.0 --runs 3
.venv/bin/python scripts/measure_app_overhead.py --turns 20
.venv/bin/python scripts/evaluate_stt.py --manifest <outside-repo>/manifest.json --provider openai --out report.json
```
Enable in a deployment (owner-approved only): `AUDLI_LATENCY_AGGREGATES=true` (server) and `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1` (web build).
