# AUD-70 — per-learner locking

Linear: AUD-70 (follow-up to AUD-68 Phase 0, which measured the problem). Draft; **not merged or deployed**. Default is **off**
(`AUDLI_PER_LEARNER_LOCKS=false`): merging changes nothing until the flag is flipped.

## 1. Problem (measured in Phase 0)
One `asyncio.Lock()` in `create_app` guards ~35 routes and is held across provider calls (STT, LLM, TTS). Two learners starting an answer together
took **1.94–2.00×** one learner's time, and a cheap lock-taking request for learner B waited **0.83 s** behind learner A's 1.0 s STT.

## 2. What the lock actually protects (audit)
| Concern | Finding |
|---|---|
| Same-learner read-modify-write across `await` (checkpoints, conversation turns, adaptation) | **Yes — this is its job.** Kept: requests of one learner still queue FIFO. Removing mutual exclusion double-evaluates and corrupts state (mutation test below). |
| Same-learner ordering across API workers | Already provided separately: `LearnerAuthentication` holds a per-learner Postgres advisory lease for the whole mutating request (`app/auth.py`). Unchanged. |
| Checkpoint/revision guarantees | DB compare-and-swap (`save_lesson(expected_revision)`), independent of the lock. Test shows exactly one of two simultaneous `/heard` calls wins, with the lock mutated away or not. |
| Cross-learner shared state | None found that depends on the lock: accent budget uses `SELECT … FOR UPDATE` on a shared row; `HomeworkEngine.cache` mutates only between awaits (single-threaded loop); `TutorSpeech` copies the provider per call, so `last_fallback` is per call; audio cache names embed the learner id; files use UUID names. |
| Neutral-prompt locks (`acknowledgment`, `retry`, `preview`) | Also process-wide, guarding **learner-scoped** caches. Converted to the same keyed lock (single-flight per learner is what they need). |
| Local/SQLite (no Postgres lease) | Single local learner ⇒ one key ⇒ identical to today. Supabase auth + SQLite (tests) relies on the keyed lock for same-learner safety. |

## 3. Design (`app/locking.py`)
`LearnerLocks` is a drop-in object (`async with lock`, `acquire/release/locked`) that maps the **server-verified** `repo.learner_id` to its own `asyncio.Lock`.
* Reference-counted entries, removed when idle (no growth; cancellation-safe: a waiter is registered before it awaits and unwound on any exception).
* `release()` uses a per-task stack, so no call site changed; releasing without holding raises `RuntimeError`.
* `enabled=False` ⇒ every key collapses to one shared key = the old global lock. Flag: `AUDLI_PER_LEARNER_LOCKS` (read at startup).
* `timed_lock` telemetry now reports queue depth **per learner**.
* No change to routes, contracts, schemas, providers, the voice, gates or adaptive logic. `app.state.locks` exposes the lock for tests.

## 4. Evidence
Measured, same harness as Phase 0 (fake providers with fixed delay, two authenticated learners, SQLite, in-process ASGI; 3 runs × 2 delays):

| | global lock (default) | per-learner |
|---|---|---|
| Two learners at once, wall / solo | **1.94–2.00×** | **1.00–1.03×** |
| Learner B lock-taking route behind A's STT | 0.27 s (0.3 s STT) / 0.83 s (1.0 s STT) | **0.007 s** |
| Queue depth at arrival | 1 for the second arrival | 0 |

Raw: `docs/AUD-68-LOCK-CONCURRENCY.json` (before), `docs/AUD-70-LOCK-CONCURRENCY-PER-LEARNER.json` (after).

Tests (`tests/test_per_learner_locking.py`, 17 cases; HTTP scenarios run with the flag off **and** on):
* concurrent learners overlap only when enabled; each learner's data stays isolated;
* same-learner requests stay serialized in both modes;
* duplicate simultaneous `assess` ⇒ evaluator and adaptation run once, identical results;
* two simultaneous `/heard` with one revision ⇒ one 200, one 409, revision advances once;
* cancelled waiter and cancelled holder (mid-provider) do not wedge the learner; next request proceeds;
* cleanup: lock table empty after every scenario, after 2000 distinct learners, and after cancelled arrivals;
* unit: FIFO, disabled-flag ≡ one global lock, exception releases, release-without-hold rejected.

Mutation checks (tests can fail): removing mutual exclusion fails 8 tests (incl. double evaluation); forcing a global key fails the two overlap tests.
Full suite on SQLite: **1158 passed** (flag off, before the new tests) and **1175 passed** with `AUDLI_PER_LEARNER_LOCKS=true`.
Full suite against a disposable local **PostgreSQL 17** container (so the advisory-lease path and the 19 formerly skipped tests run):
**1759 passed, 21 skipped with the flag off and 1759 passed, 21 skipped with it on.** The repo's CI "PostgreSQL integration" job is the independent check.

**Not verified:** real production concurrency, ElevenLabs behavior under parallel load, multi-worker deployment with the flag on.

## 5. Risks
1. **More simultaneous provider calls.** Previously ≈1 locked provider call at a time app-wide. Now one per active learner. OpenAI rate limits are rarely the issue at this scale, but **ElevenLabs plans cap concurrent requests**; a 429 makes the router fall back to the OpenAI voice (voice identity degrades). Lesson/classroom/onboarding speech already runs without the lock today, so exposure exists but grows. Watch `Tutor TTS … fallback` events / `X-Audli-Tutor-Fallback`. If fallbacks rise, add a process-wide cap for ElevenLabs synthesis (not included; needs the plan's real limit — unverified).
2. **Event-loop contention.** Synchronous DB calls and JSON work still run on the one loop; benefit depends on that being small. Phase 0 measured app overhead ≈100–130 ms/turn, so it should be.
3. **Single process only.** The lock is in-process. Multi-worker same-learner ordering relies on the Postgres lease (unchanged); SQLite deployments must stay single-worker.
4. **Same-task release.** Acquire and release must run in one task (true for every `async with` site). Violations raise instead of silently corrupting.
5. **Key source.** Derived from `RequestRepository` (verified identity). A client-supplied id is never used.

## 6. Rollout / rollback
1. Merge with the flag **off** (no behavior change). 2. After the Phase 0 telemetry is live, turn the flag **on** in a single environment, compare `turn.*.lock_wait`, per-route p90 and tutor-fallback counts. 3. Roll back by setting it **off** (restart); no data or schema involved.

## 7. Decisions for the owner
* Approve merging with the flag off, then a staged enablement after production latency validation?
* ElevenLabs plan's concurrent-request limit (to size the optional synthesis cap)?
