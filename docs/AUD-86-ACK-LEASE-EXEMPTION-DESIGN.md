# AUD-86 — Exempting the acknowledgment-audio endpoint from the account lease (design only)

Status: **proposal, nothing implemented, nothing deployed.** Needs explicit owner approval before any code. Evidence base: AUD-85 diagnosis (`docs/AUD-85-TURN-LATENCY-DIAGNOSIS.md`).

## 1. Problem (measured)
`POST /api/recognition/acknowledgment-audio` is a POST, so `LearnerAuthentication` makes it take the per-account Postgres advisory lease (`pg_try_advisory_xact_lock`), which `POST /attempts/{id}/assess` holds for its whole ~9 s. The cue therefore returns only after the assessment finished (7.5 s in the traced turn, 6–11 lease retries), arrives too late to cover the wait, delays the coaching request by ~1.2 s (the browser requests coaching when the cue request returns) and, because every retry opens a new database connection synchronously on the event loop (~0.7 s each), blocks the loop for most of that wait.

## 2. Correction to the premise: the endpoint is not purely static
The handler (`app/lesson_api.py`, `acknowledgment` → `speech`) is neutral text from a four-entry constant table, but it is not free of learner-scoped effects:

| Step | What it touches | Mutates? |
|---|---|---|
| `Bearer` verify, `for_learner` scope check | Supabase Auth call, one ownership `SELECT` | no |
| `repo.profile()` | reads the learner's `tutor_voice` (chooses voice/provider signature) | no |
| `repo.audio_blob(name)` | `SELECT` on `audio_assets` by `(audio_name, user_id)`; `name` = sha256(learner id, voice signature, key, text) | no |
| cache **miss**: `limits.admit(...)` | reads lesson/entitlement state; in `enforce` mode may refuse (429) | no (reads) |
| cache miss: `tutor.speak` + `sanitize` | billable TTS call, no database | no |
| cache miss: `repo.cache_lesson_audio` | **`INSERT … ON CONFLICT` into `audio_assets` (user_id FK → users)** | **yes: a derived cache row** |
| `acknowledgment_lock` | in-process asyncio lock for this cue route only | n/a |

So "exempt it as is" would let one learner-scoped **write** (the cache row) run without the lease. That row is content-addressed and idempotent, touches no progress/lesson/conversation table, and `check_audio_owner` refuses another learner's name. Its only race is with account reset/deletion (the lease exists so "Reset cannot run while another worker saves"): an insert landing after the user row is deleted violates the foreign key (a 500 for an optional cue), and one landing between a reset's delete of `audio_assets` and its commit could resurrect one cache row.

## 3. Options
**A. Exempt the route unchanged.** One-line change to `LEASE_EXEMPT_ROUTES`. Keeps the cache write and the limits check. Residual risk: the reset/deletion race above and a billable provider call outside the lease. Not recommended.

**B. Exempt the route and make it strictly read-only (recommended).** Behind a flag `AUDLI_ACK_LEASE_EXEMPT` (default off):
1. Exact route `POST /api/recognition/acknowledgment-audio` joins `LEASE_EXEMPT_ROUTES`, nothing else (same one-method, one-path shape guard as the existing exemptions).
2. When the flag is on the handler stops using `speech()`: it reads `tutor_voice` (one read, as now), looks the audio up in a **process-level in-memory cache keyed by (voice signature, cue)**, and on a miss generates it once (single-flight) and keeps it in memory only. **No `audio_assets` write, no `limits.admit`.** The four phrases × the handful of voices make this a few provider calls per process lifetime.
3. The per-learner cached audio row is no longer written by this route (existing rows remain valid and unused). With the flag off, behaviour is byte-for-byte today's.

Open product question for the owner: dropping `limits.admit` means the cue is never refused in cooldown (it is only ever requested inside an open lesson) and a cache miss is no longer attributed to the learner's allowance. The cost is the same few TTS calls per process, shared by all learners.

## 4. Authorization requirements (unchanged and tested)
* Authentication stays first: the exemption is evaluated after `identity.verify` succeeds, exactly like `/api/telemetry/turn` and Review improvement; 401/503 paths are untouched.
* Exact method + path (no prefix, case, slash, encoding or sibling variants; the near-miss list from `test_auth_lease_exemption.py` is reused).
* Outer origin guard and body-size limit still run before it.
* Learner identity comes only from the verified token; the cue body is the existing strict schema (`cue` ∈ assessment|followup|reflection), no learner or voice input from the browser.
* Every other mutating route still queues behind the lease (the existing every-route test stays as is and must not change).

## 5. Proof that it cannot mutate learner state (tests to write)
1. **Statement-level:** during the request every executed statement must be a plain `SELECT` (reuse the AUD-84 P2 read guard on the repository engine and additionally assert no `transaction()` is opened: `transactions == 0`, `commit`/`rollback` stage counts zero).
2. **Golden dump:** a full dump of every table before and after N requests (all cues, hot and cold cache, each voice) is identical (the technique of `test_state_only_ready.py`).
3. **Structural (AST):** the handler body and the helper it calls may only reference an allow-list (`repo.profile`, the memory cache, `tutor.speak`/`sanitize`); a new reference fails the test, as `test_exempt_path_is_one_post_route…` does today.
4. **Cache correctness:** memory cache keyed by voice signature never serves one voice's audio for another; a learner switching voice gets the new voice at the next cue.
5. **Failure isolation:** provider failure returns the existing optional-cue failure (503) and never touches the lesson; the browser already treats the cue as optional.

## 6. Concurrency tests (PostgreSQL, real lease)
1. Hold the account lease from a second connection: the cue returns promptly (< 1 s with a stubbed provider) while an ordinary POST stays queued behind the lease. `lease_attempts` for the cue is 0 in the AUD-85 trace.
2. `/assess`-like long request holding the lease + concurrent cue: cue completes first; the long request's result and database dump are identical to a run without the cue.
3. 50 parallel cues for one voice: exactly one provider call (single-flight), all responses identical.
4. Account reset/deletion concurrent with cues: no row created, no 500, reset result unchanged.
5. Event loop: with the cue running during an assessment, no lease connection is opened for it (`lease.checkout` count unchanged), measured by the AUD-85 stall attribution.
6. Flag off: today's behaviour exactly (cue queues behind the lease; cache row written).

## 7. Expected effect
Measured baseline: cue 7.5 s, coaching request starts 1.2 s after the assessment. Expected (estimate, to be measured with the AUD-85 trace): cue returns in ~1–1.5 s (auth, scope, one read, cached audio), so it plays while the assessment runs as originally intended; coaching starts as soon as the assessment ends (≈ −1.2 s on the critical path); the loop is no longer blocked by cue lease polling (stall reduction not yet quantified).

A browser change is **not** required for correctness. Whether the cue should then be requested earlier than the current 1.2 s is a separate product decision.

## 8. Rollback
Unset `AUDLI_ACK_LEASE_EXEMPT` on Render (about a minute) or revert the PR. No schema or data change; nothing to clean up (the memory cache vanishes on restart; existing `audio_assets` rows are never touched).

## 9. Risks and open questions
* Owner decision on dropping `limits.admit` for the cue (above).
* Fallback voice: the response headers from `tutor.headers` (e.g. `X-Audli-Tutor-Fallback`) must be preserved; a fallback-generated cue must not be cached as the primary voice (the current code skips the cache write for fallbacks; the memory cache needs the same rule).
* Single instance today; with several instances each warms its own memory cache (harmless).
* Security review of the widened exempt set is required, exactly as for AUD-68/AUD-77.
