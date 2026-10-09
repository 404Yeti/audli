# AUD-21 conversational latency review

Work remains In Progress. A reviewed local AUD-21 commit is now authorized after
verification; push, deployment and issue closure remain unauthorized. Earlier sections
record historical verification before that authorization.

Manual acceptance instructions: [AUD-21-MICROPHONE-ACCEPTANCE.md](AUD-21-MICROPHONE-ACCEPTANCE.md).

## AUD-23 acceptance

Owner-confirmed deployment: `1fb9eb5252fa21b63cf5b8ff5b9d06d4a83b8635`, Render Live,
FastAPI startup complete, PostgreSQL migration ledger `[1, 2]`, backup integrity verified.
These confirmations are not independently executed authenticated acceptance tests.
AUD-23 remains In Progress. The Render connector requires explicit workspace selection;
no authorized production browser/session has been supplied in this session.

Manual smoke test with an existing authorized learner:

1. Sign in, GET `/api/profile`, verify existing saved preferences and progress against known records.
2. GET `/api/history`, verify previous completed work remains accessible.
3. GET `/api/lessons/current`, verify the existing phase/exercise/pending response.
4. Reload an existing lesson and verify its persisted checkpoint and topic. POST
   `/api/lessons/{id}/resume` changes paused lesson state; execute it only with explicit
   owner authorization on a dedicated test lesson. Spoken continuation can also write state.
   Verify hidden transcripts remain blocked until a successfully evaluated attempt.
5. Review Render logs from deployment startup and throughout these requests for startup errors
   and unexpected 5xx. Record UTC window, deployment URL, statuses and redacted results.
6. Add evidence to Linear and seek owner approval for closure only after acceptance passes.

## Baseline and measurement limits

There is no representative measured production end-to-end baseline yet. The issue's
manual observation of roughly 5–8 seconds is not a p50/p90 dataset. Do not claim the
≤5-second median target is achieved.

Controlled microbenchmark, 12 HTTP requests per version, identical synthetic 200ms TTS
wait, generated 3-second WAV, SQLite persistence and real sanitization:

| Repeated static onboarding prompt | p50 | p90 (nearest rank) | TTS calls |
|---|---:|---:|---:|
| Before cache | 397.0 ms | 400.9 ms | 12 |
| After cache, including first cold request | 3.0 ms | 4.0 ms | 1 |

Before samples (ms): 587.8, 386.4, 393.8, 389.2, 400.7, 396.1, 398.0, 396.1,
385.7, 397.9, 399.6, 400.9.
After samples (ms): 529.5, 3.4, 3.2, 3.4, 3.4, 2.7, 2.4, 2.6, 2.8, 4.0, 2.8, 2.7.

The first cold request is still expensive. These numbers measure complete HTTP body
availability for repeated prompts, not speech end to audible response. They contain
no real provider variability or browser/network playback. Reproduce the current
workload using `.venv/bin/python tests/latency_benchmark.py`; compare the same script
against the uncached revision in an isolated checkout. Results vary with hardware.
An additional current-code run (12 requests) measured p50 3.9ms / p90 4.6ms,
with one TTS call and a 429.9ms cold request. This is host variability, not measured
live provider variability.

## Pipeline and bottlenecks

| Path | Ordered operations before meaningful response |
|---|---|
| Onboarding | silence → recorder finalization → upload/validation/STT → pending checkpoint → extraction → profile checkpoint → next static prompt TTS/sanitization → full body → playback |
| Welcome | silence → upload/validation/STT → pending checkpoint → check-in response provider → checkpoint → TTS/sanitization/cache → full body → playback |
| Review | silence → upload/validation/STT → pending checkpoint → interests extraction → checkpoint → personalized response TTS → full body → playback |
| Exercise | silence → upload/validation/STT → attempt checkpoint → evidence assessment → deterministic follow-up or final adaptation/persistence → gated cue TTS/cache → full body → playback |

The detector waits 1,800ms after the last voiced sample, polls at 50ms, and requires
at least 2 seconds from recording start. Last detected voice is an estimate; room noise,
quiet speech and brief pauses affect it. Reducing this without representative speech
can truncate learner evidence, so detection policy is unchanged.

Assessment must precede evidence-grounded coaching. Application decisions/persistence
remain authoritative and happen before feedback. Profile extraction happens once per
accepted onboarding stage and once per lesson reflection, not on every comprehension
answer. The comprehension provider already returns structured evidence in one call;
follow-up choice and terminal feedback are application code, not additional generation calls.

The browser already requests coach audio inline with `Accept: audio/*`, avoiding a
second fetch. Playback still waits for the entire sanitized audio body. Server-side
sanitization and durable caching precede delivery; genuine provider streaming needs
an independent design, including metadata stripping, ownership and failure semantics.

Neutral acknowledgment preparation runs concurrently with assessment without changing
state. Its 1,200ms timer begins after transcription. An acknowledgment already playing
can hold up meaningful coaching until it ends. This is distinct from assessment time;
it remains a candidate for measured optimization with cancellation/duplicate tests.
Per-learner locks currently cover provider work; queueing is measured separately.
Do not release those locks opportunistically or run stateful requests concurrently.

## Implemented changes

- Reuse sanitized, durably cached static onboarding prompts on retry/revisit. Key includes
  verified owner, full prompt, provider, speech model, voice and version. No client cache,
  learner recording retention, profile shortcut, or new provider call. Revision/auth gates
  run even on cache hits. Failed synthesis does not poison the cache.
- Separate meaningful response, neutral acknowledgment and recognition retry timing.
  Only the first `playing` event of meaningful speech consumes the turn timer; replay
  does not count again. End/cancellation resets it; new recordings replace the boundary.
- Distinguish request submission, headers, first nonempty audio body chunk and full-body
  readiness. Reading continues to buffer the complete body, preserving delivery behavior.
- Add request-local `Server-Timing` metadata for provider, validation, persistence,
  adaptive decisions and lock waits, including the previously uninstrumented lesson path.
  No contents, credentials, IDs or personal information are placed in timing headers.

Timing is enabled locally in development. Explicit production opt-in:
`AUDLI_LATENCY_TELEMETRY=true` for server headers and
`NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY=1` at frontend build time for console timing.
These settings have not been deployed. Browser timings remain local numeric console
metadata; there is no external collector or stored learner telemetry. Server operation
logs retain their existing development-only policy. `Server-Timing` stages may nest;
do not sum inclusive spans or lock waits twice. Header timings end before body transport.

## Ranked follow-up proposals

| Proposal | Expected benefit | Complexity | Quality/recovery risk | API cost |
|---|---|---|---|---|
| Static prompt cache (implemented) | Removes TTS and sanitization on repeat only | Low | Low; owner/config/text keyed, durable, gated | Lower on repeat |
| Let completed assessment end optional acknowledgment playback | Removes remaining acknowledgment duration when assessment finishes mid-cue | Medium | Must test End, replay, account switch and delayed play promise | Unchanged |
| Tune detector using representative short/long answers and natural pauses | Potentially saves part of 1.8s silence | Medium | Can truncate evidence; requires observed recordings and accuracy evaluation | Unchanged unless retries rise |
| Overlap independent restore reads | Small network saving before conversation starts | Low | Account-bound cancellation still required; outside primary turn target | Unchanged |
| Combine upload/STT and assessment transport | Saves one round trip, not provider work | High | Recovery checkpoints, uncertainty, idempotency and ownership changes | Usually unchanged |
| Streaming speech delivery | Removes full-body wait | High | Sanitization, partial failure, authorization and cache consistency | Provider-dependent |

## Representative before/after protocol

Use an authorized test learner and equivalent saved exercise/profile workloads on the
same backend region, browser, device and network. Include welcome, reflection, initial
summary, follow-up, short answers and extended answers; record cold and warm cues
separately. Gather at least 30 reliable evaluated turns per comparison (prefer more
for a stable p90), alternate versions/time windows, and report failures/retries as well
as successful samples. Do not include neutral acknowledgments in meaningful latency.

Collect numeric browser milestones plus same-request Server-Timing headers. Use an
approved laboratory reference for the actual input speech-end boundary and output
loopback onset; `playing` and microphone RMS are only estimates and do not prove actual
acoustic onset. Do not retain raw learner audio or transcripts in telemetry. Provider
variability requires repeated live requests and p50/p90 per path, provider configuration,
sample sizes, confidence/uncertainty exclusions and network conditions. Keep all
provider/models and exercise difficulty equivalent; do not infer quality from mocks.

## Verification

Verified locally:

- Complete backend suite with SQLite and disposable UTF-8 PostgreSQL 18: **477 passed**.
- Focused onboarding/cache and timing/privacy checks: **28 passed**.
- Frontend helpers, including delayed audio and acknowledgment timing: **47 passed**.
- Standard Chromium suite, including new meaningful-response timing coverage:
  **73 passed, 1 skipped**. The skipped test requires the separate integrated-media configuration;
  it was not executed and is not claimed to pass.
- ESLint and standalone TypeScript: passed.
- Production build: passed (`next build --webpack`), including TypeScript and static page generation.
- `git diff --check`: passed.

The initial restricted backend run stalled and was interrupted; the unrestricted full
suite passed. Initial PostgreSQL attempts used an incomplete URI and then a SQL_ASCII
cluster; Unicode JSON failed in that cluster. The final complete run used a UTF-8
disposable cluster and passed without application changes to bypass those errors.
The interrupted Chromium download was completed before the passing browser run.
Both disposable test servers were stopped after verification.

Production authenticated acceptance and representative live latency remain outstanding.

## Continuation: production access and real-microphone measurement

The continuation preserves all earlier uncommitted changes. No production requests or
state changes have been made. Linear still reports AUD-23 and AUD-21 In Progress.
The Render connector again returned `no workspace selected`; its tool contract requires
explicit workspace confirmation. No authorized production browser/session has been
identified. WSL exposes its audio bridge, but that does not establish an available
human speaker, authorized browser microphone session, or recorded conversational sample.

Representative real-microphone results in this continuation:

| Turn type | Human microphone samples | First acknowledgment p50 / p90 | First meaningful audio p50 / p90 |
|---|---:|---|---|
| Check-in | 0 | Unavailable | Unavailable |
| Exercise answer | 0 | Unavailable | Unavailable |
| Coaching/follow-up | 0 | Unavailable | Unavailable |
| Transition | 0 | Unavailable | Unavailable |

Stage distributions and equivalent real-microphone before/after comparisons are also
unavailable. The earlier cache microbenchmark remains separate. Neither RMS last activity
nor a browser `playing` event establishes actual acoustic end-of-speech/output onset.

Pending production smoke checklist (read-only):

- In an authorized existing learner session, read `/api/profile` and `/api/history`;
  compare known saved preferences, progress and completed work without logging personal data.
- Read `/api/lessons/current` and `/api/lessons/{id}`; check that reload restores the same
  saved phase, exercise and pending response. Do not start a new lesson or submit an answer.
- Check `/api/exercises/{id}/transcript`: an existing unassessed exercise must return 403;
  an already successfully assessed, owned exercise must return 200. Do not evaluate a new
  attempt just to make this check possible. A known exercise belonging to a separate
  authorized test account must be inaccessible without disclosing its contents.
- Inspect deployment startup and 5xx logs after the Render workspace is confirmed.
  Record UTC windows, commit/deployment reference, statuses and redacted outcomes.
- A paused lesson's POST `/resume` writes lifecycle state. Under the current prohibition
  on production data modification, restrict agent checks to reload/read-only restoration;
  do not execute that mutation. Actual paused-to-active acceptance remains pending unless
  the owner explicitly authorizes it on a dedicated test lesson.

For real-microphone testing, identify the browser/device and authorized test learner,
then capture human spoken check-ins, short and extended summaries, natural pauses, and
follow-up answers with the same device/network/provider/voice/exercise difficulty.
Use a dedicated nonproduction learner for repeated baseline comparisons. Keep the current
checkout intact; use an isolated baseline checkout and independent local data copies
created from the same nonproduction starting checkpoint. Do not reset production profiles.
Capture numeric timing only, without retaining raw learner audio, transcripts, tokens or
profile contents. Report each version's sample sizes and reliable/uncertain/failed turns.
An acknowledgment absent on a fast turn is missing, never a zero-latency sample.
For transitions without new learner speech, report transition-ready → playback separately;
do not manufacture an end-of-speech origin or include them in the target median.

### Next smallest implementation proposal

The current caller waits for acknowledgment playback to finish before it asks for
meaningful coaching TTS. Once authoritative assessment has completed and persisted,
prepare the owned/gated meaningful audio concurrently with the remaining optional
acknowledgment playback, while keeping one playback owner and normal acknowledgment
completion. Do not start TTS from preliminary evidence or duplicate assessment.

For remaining acknowledgment duration A and meaningful TTS/delivery duration T, the
current additional wait is A + T. Safe overlap would make it max(A, T), saving up to
min(A, T). These are scheduling relationships, not measured representative savings.
Test account changes, End, replay, delayed bodies/play promises, stale cues, TTS failure,
retry deduplication and checkpoint recovery before implementing. API call count should
remain unchanged. Begin with this preparation overlap rather than lowering the silence
threshold; it does not shorten a learner's opportunity to finish speaking.

A new detector regression preserves a 1.5-second mid-answer pause followed by more
speech. At recording time 2000ms, after the last voice at 500ms, a 1200ms threshold
would already end the turn while the current 1800ms threshold waits. This controlled
trace demonstrates a clipping risk; it does not establish a safe shorter threshold
for representative real speech. Detection settings are unchanged.

### Controlled follow-up evidence and verification

The scheduling experiment passed in Chromium: after the mocked authoritative assessment
completed, no new `/coach-audio` request was issued while the acknowledgment remained
in playback across an additional 5000ms of virtual time. Emitting `ended` immediately
allowed the coaching request. This proves the client dependency; the held audio,
provider results, microphone and time are fixtures. The 5000ms hold is test-imposed,
not an observed acknowledgment duration or production latency sample.

Focused acknowledgment/meaningful timing/retry/delayed-audio Chromium tests: **4 passed**.
Timing/recognition/turn-detector helpers, including the new pause regression: **10 passed**.
Application behavior and provider configuration were not changed in this continuation;
only evidence tests and this report were added. Earlier complete suite results remain
historical verification, not newly executed full-suite results.

Continuation final verification: focused ESLint, TypeScript and `git diff --check` passed.
All prior uncommitted application changes are preserved; neither issue was marked Done,
and no commit, push, deployment or production mutation was performed.

## Approved optimization: coaching preparation during acknowledgment

This implementation supersedes the sequential scheduling behavior documented above.
Only a successful authoritative `/assess` response starts preparation, and only while
an acknowledgment is playing. Follow-up and terminal feedback use the existing owned,
evidence-gated, binary `/coach-audio` endpoint. `PreparedAudio` owns one preparation and
one playback per operation/cue/checkpoint. Background failures are observed immediately
and surfaced through the existing retry flow after acknowledgment ends. Preparation
creates no object URL, marks nothing heard, and never advances the lesson.

Playback remains serial: acknowledgment must finish before prepared coaching is played.
End, account changes and unmount cancel the operation; late bodies cannot create an
object URL or play. Unexpected refresh restores the saved authoritative conversation,
rather than an in-memory preparation; the existing durable server audio cache avoids
another successful synthesis for the same cue. A failed synthesis can be retried;
completed learner assessment is retained. The server checks optional paired lesson
ID/revision fields before synthesis/cache delivery and again after provider completion.
Background playback also reloads the checkpoint before consuming prepared audio,
rejecting stale revision, phase, exercise or status. This adds one owned read request
for an overlapped handoff; no extra provider call is intended. Existing cue-only clients
remain compatible. Silence detection remains 1.8 seconds.

Files in this focused change: `web/lib/prepared-audio.ts`, `web/lib/voice-lifecycle.ts`,
`web/app/page.tsx`, `web/lib/turn-timing.ts`, `app/conversation_api.py`,
`tests/conversation.test.mjs`, `tests/test_lesson_lifecycle.py`,
`web/tests/structured-lesson.spec.ts`, and this report. Earlier telemetry/cache changes
remain uncommitted and preserved.

### Timing evidence and limitations

An equivalent controlled Chromium scenario formerly issued no coaching request during
an extra 5000ms acknowledgment hold after saved assessment. The updated scenario prepares
coaching before that hold ends, issues one coaching request, and plays it once only after
`ended`; readiness never skips feedback or triggers `/ready`/`advance` early.

One instrumented virtual-clock/mocked-provider run recorded terminal coaching readiness
**22ms**, acknowledgment playback **5246ms**, and coaching wait after acknowledgment
**20ms** (including the checkpoint read, ending immediately before `speak`). The earlier
1025ms readiness event belongs to the summary question, not terminal coaching.
The acknowledgment hold is imposed by the test; these are scheduling diagnostics,
not acoustic measurements or a production baseline. Virtual time still advances during
real browser/network work, so even these fixture values vary between runs. A second complete-suite run recorded terminal readiness 22ms, acknowledgment
5239ms and post-acknowledgment wait 16ms, again with the imposed hold. The additional
5000ms head start is a verified ordering difference, not a 5-second observed speedup.

The expected removed sequential wait is the overlap between remaining acknowledgment
playback and coaching preparation, minus checkpoint-validation overhead. Meaningful
coaching still cannot start before acknowledgment ends, and slow synthesis still adds
its unfinished portion afterward. Representative real-microphone samples remain **n=0**;
end-of-speech p50/p90, provider variability and the ≤5-second target are unverified.
Separate numeric privacy-safe stages now include `acknowledgment_duration`,
`coaching_readiness`, and `coaching_wait_after_ack`. The last stage measures the
post-acknowledgment preparation/validation wait; actual `playing` is still measured
separately by the existing playback and meaningful-response events.

### Overlap verification before local-commit preparation

- Full backend suite against SQLite and isolated UTF-8 PostgreSQL: **481 passed**.
- ESLint, TypeScript, production build and `git diff --check`: passed.
  The restricted build was interrupted after worker stalls and completed successfully
  outside the sandbox; generated `next-env.d.ts` changes were restored.
- Frontend helpers: **51 passed**, including preparation singleflight, exactly-once
  playback, observed failures/fresh-operation retry, cancellation and stale validation.
- Focused backend suite: **104 passed**. Cache deduplication, transcript gating,
  unchanged lesson checkpoint and End during synthesis are covered.
- Full Chromium suite: **79 passed, 1 skipped** (optional integrated real-backend
  media test; not a microphone/provider measurement). Initial new harness expectations
  were corrected, and an unrelated reload-fixture navigation race passed on rerun.
  Chromium verifies early readiness/serial playback, slow preparation, failed
  preparation/retry without reassessment, End, revision changes, refresh, and the
  existing authentication, recovery and reduced-motion behavior.

Remaining stage costs are the fixed silence window, upload/transcription, authoritative
assessment/response composition, uncached TTS, full-body buffering and playback startup.
Next step is representative microphone measurement with equivalent workloads, including
separate acknowledgment and meaningful coaching p50/p90 and sample sizes. Do not shorten
the silence window based on these fixtures: the previous 1.5-second mid-answer pause
regression rules out the proposed 1.2-second threshold.

AUD-21 and AUD-23 remain In Progress. No production acceptance was performed in this
implementation turn, and there is no commit, push, deployment or issue closure.

## Local-commit review and microphone acceptance preparation

Reviewed the entire AUD-21 diff: server/browser numeric timing, static prompt caching,
saved-assessment coaching preparation, revision/cancellation protection, regression
fixtures, synthetic benchmark and documentation. No unrelated application changes,
actual credentials, learner data, audio/database files or generated artifacts are included.
Credential-like strings found by the review scan were preexisting synthetic diagnostics
fixtures. Generated Next.js declarations were restored and build/browser output remains
ignored. The four-line `web/tests/lesson-fixture.ts` change returns an empty current lesson
without querying a page clock during reload, removing a reproducible fixture navigation
race while preserving the application's authentication behavior.

Fresh pre-commit verification:

- Backend SQLite + isolated UTF-8 PostgreSQL: **481 passed**.
- Frontend helpers: **51 passed**.
- Final complete Chromium run: **79 passed, 1 skipped**, including auth reload and all
  preparation/cancellation/revision/retry cases. The skipped integrated-media test is
  not claimed to pass. Startup used a temporary configuration with a 300-second server
  timeout after the mapped-filesystem startup exceeded the standard 120-second limit;
  the temporary configuration is not committed.
- ESLint, standalone TypeScript and final sequential production build: passed.
- Numeric manual-collector smoke check: passed (observer markers, separate stages,
  missing acknowledgment and cleanup). `git diff --check`: passed.

An initial concurrent browser/build check read a partially regenerated route declaration
and failed. The final build ran after the browser server stopped and passed compilation,
TypeScript, static generation and optimization. No application workaround was added.
The disposable PostgreSQL server was stopped after testing.

[AUD-21-MICROPHONE-ACCEPTANCE.md](AUD-21-MICROPHONE-ACCEPTANCE.md) supplies the concise
manual checklist, production-mode telemetry setup, observer marker collector and exact
p50/p90 protocol. Browser last-voice/`playing` estimates are explicitly distinct from
human-observed final speech/first audible words, with observer uncertainty reported.
Representative samples remain **n=0**. No latency target achievement, production
acceptance, push, deployment or issue closure is claimed. The owner authorized only a
verified local AUD-21 commit; its resulting SHA is recorded in Linear and the handoff.
