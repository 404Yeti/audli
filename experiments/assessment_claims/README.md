# AUD-94 — Staged authoritative assessment ownership

Review branch only. **No production activation, schema migration or deployment is provided.** Explicit installation accepts only disposable `audli_claims_*` PostgreSQL databases on loopback and mocked-provider configuration. Application startup does not import this package. PR110/111 and their branches remain unchanged.

This stage builds on the optimized3d7fb27 claim/read/write transactions. It extends the same account slot and monotonically increasing fence to upload, assessment, application-owned follow-up selection, final evaluation, adaptation, readiness and lesson advancement. Two short transactions surround provider work. The full-path results and correctness record are in RESULTS.md.

## Authoritative transactions

- Claim locks verified owner profile, account slot, lesson and conversation, then reads one batched private snapshot. Operation input, owner, attempt/question, fingerprint, pending evidence and lesson state are validated by server code. Confirmation/recognition gates use the existing strict request/Transcription schemas and confidence logic. The claim and durable ASSESSING/pending-text checkpoint commit atomically before the provider runs.
- Provider calls hold no request-owned database connection or advisory lock in claims-only mode. The provider result must pass EvidenceAssessment parsing and the existing validate_evidence grounding/completeness checks. No client-supplied score or verdict is accepted.
- Finalization reacquires ownership, verifies token/status/expiration/input and the entire authoritative snapshot, and calls existing choose_followup/final_evaluation/adapt_final/final_feedback functions. Persisted turn/assessment/evidence and either the issued follow-up or final evaluation/adaptation/profile/session changes commit with operation completion and slot release. Profile changes preserve the original one-variable/unknown-evidence policy; uniqueness constraints remain backstops.
- Readiness and lesson advancement have their own stable operations. Advance requires the current lesson revision, evaluated exercise and READY_FOR_NEXT. It atomically recomputes the completed-exercise count, clears the current exercise, increments the lesson revision and composes closing state when due. It does not regenerate speech.
- A completed operation replays current canonical state, not a stale prior question. Concurrent finalizers cannot append a second turn or adapt twice. Recognition uncertainty remains uncertainty, including when multiple finalizers replay it.

This does not migrate the entire lesson lifecycle: generation, check-ins, tutor TTS, closing/homework completion and ordinary onboarding remain next-stage work. Claims-only mode rejects unconverted HTTP mutations before their handlers. That is a deliberate review-stage boundary, not production-compatible client behavior.

## Conflicting writers and database guard

Every current learner-owned relational table is guarded: users, profiles, sessions/exercises/attempts, conversations/turns/followups/assessments/evidence, evaluation/adaptation rows, lessons/links/entitlements, owned audio/coaching caches, roadmaps, homework/reflections and review-improvement rows. A regression checks this inventory against schema.NOT_LEARNER_OWNED. Global catalog/budget/config tables are excluded intentionally.

The SECURITY INVOKER trigger derives row ownership through direct owner or parent exercise/attempt, rejects owner reassignment, and requires the active transaction-local owner/operation/fence context while an operation is live. Unknown writers cannot silently overwrite pending evidence, change a question, reset a profile or complete an exercise underneath the provider. The slot remains exclusive even if its timestamp has expired until a legitimate claimant or invalidator handles it.

Converted writers lock profile before slot and domain rows. An unconverted row writer uses a NOWAIT profile check in the trigger to avoid waiting in an inverted row/profile lock cycle. A conflict aborts its entire transaction. This prevents deadlocks from becoming an unbounded application wait; callers normalize failures without logging SQL bound values or private inputs. It is not a replacement for migrating writer entry points.

Known reset/pause/expiry writers revoke the active operation and rotate the fence in the same transaction as their mutation. Deadline settlement preserves existing closing/grace behavior; actual expiry preserves accepted work and is never completion. Invalidating reset preserves captured attempts. Explicit confirmed onboarding restart retains its original unevaluated-work discard semantics and allowance checks; stale reset revisions roll back invalidation too.

Record again is explicit and CAS-bound to the pending attempt and question. It invalidates any in-flight assessment, clears only the pending pointer and keeps the recorded evidence row. Another upload cannot silently replace unresolved evidence. Production-client integration of that endpoint is still a gate.

## Fault/recovery semantics

- Normal duplicate requests join processing or replay completion without another provider invocation. Stable upload IDs and P2 source/cap/deadline are unchanged.
- Worker death leaves a durable expiring claim. Takeover rotates the token; an old result cannot mutate state. A crash after provider completion but before durable finalization can still repeat an external call. Exactly-once provider billing is not promised.
- The shared cancellation helper waits for a started DB transaction to settle and bounds cancellation waiting. It never releases ownership underneath an unknown writer. Provider cleanup failure leaves a fenced claim until expiry rather than inventing a successful release. Responses and logs use fixed messages, not provider text/SQL parameters.
- Audio stays in memory and is closed after upload processing. No raw audio persistence is added. Reload recovery needs the next-stage typed status/resume client and honest loss disclosure when the Blob is gone.
- Keep the prior60-second ownership TTL/50-second P2 deadline policy: delivery timeout means unconfirmed, not permission to steal. No transport timeout, codec, provider or routing setting changes.

## Compatibility and activation gates

See DEPLOYMENT.md. Legacy-first and bridge-first real-middleware tests retain the original advisory lease across the request and demonstrate one assessment and one issued question. Bridge mode therefore still holds a connection during provider work. Claims-only zero-checkout measurements apply only after legacy workers are drained.

The local schema is an implementation/rehearsal schema, not a versioned production migration. Function permissions, deployment version negotiation, all provider routes, account retention/deletion, typed processing recovery, physical Safari acceptance and production capacity/observability remain gates. The original upload-only PoC schema and this generic staged schema must not be mixed blindly; unknown active ownership is rejected.

## Reproduce

Use the same pinned local Python environment and disposable PostgreSQL17.10 setup as the optimized PoC. The parent experiments/durable_claims/local.py helper creates/drops unique loopback databases. No production credentials are read. Install schema/guards only through this module's explicit local installer.

```bash
python -m pytest -q experiments/assessment_claims/test_stage.py experiments/assessment_claims/test_additional.py
python scripts/pg_delay_proxy.py --listen 55451 --target 55449 --one-way-ms 36
python -m experiments.assessment_claims.benchmark --root /path/to/source --mode staged --output /tmp/staged.json
```

For the control use a clean732e420 checkout and mode=current. Both use real HTTP, actual unchanged P2 sender,72ms nominal DB RTT,300ms mocked auth,1100ms STT and3500ms assessment, main pool3/zero overflow,32 executor threads and the same flags. The workload is two spoken recordings/assessments (one follow-up), readiness and persisted lesson advance. It excludes generation, TTS/playback and physical recording/network capture. Local setup clears synthetic data between waves outside timers; guards remain installed/enforced during every measured request. No pool/timeout changes or admission waiting are used.
