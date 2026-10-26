# AUD-89 P2 — Recording delivery recovery

Three reviewable PRs: A policy and server characterization; B sender and reconciliation; C recovery UI and browser coverage. Merging does not authorize deployment or activation. No CORS/routing changes, audio persistence, provider experiments, or production flag changes.

## Policy and kill switch

Authenticated, read-only `GET /api/upload-policy` returns `{"enabled": false}` unless BOTH `AUDLI_UPLOAD_RETRY` and `AUDLI_UPLOAD_IDEMPOTENCY` are enabled. Retry defaults OFF. Malformed JSON overrides, unknown fields, wrong types and out-of-range values fail closed with a fixed warning containing no configuration values. API responses are no-store. Client parsing uses the same rejection fixtures as the server.

Defaults: base_ms=8000, bytes_per_second=64000, min_ms=9000, max_ms=30000, growth=[1,1.25,1.5], max_sends=3, deadline_ms=50000, backoff_ms=[300,900], slow_hint_ms=3000. No hidden-page allowance: every actual send counts.

Per-send timeout in milliseconds is `min(max_ms, clamp(base_ms + bytes / bytes_per_second * 1000, min_ms, max_ms) * growth[index], remaining_wall_ms)`. The final cap applies AFTER growth. A 50-second WALL-CLOCK budget covers policy acquisition, authentication, upload, response body, reconciliation and backoff. It need not fit three sends. No sends begin while hidden. Pagehide stops delivery; return requires explicit recovery. Cancellation ends waits even when fetch/auth ignores abort. Late results cannot update UI or initiate another fetch.

Policy reads have a short independent timeout and fail closed. Enabled policies are cached at most one minute; revalidated before each automatic resend. A failed refresh disables subsequent automatic sends. Rollback: set AUDLI_UPLOAD_RETRY=false/unset on Render, deploy the configuration, verify health and the authenticated policy endpoint, and allow up to the client cache TTL before open clients stop resending. This is NOT instantaneous; active requests cannot be recalled. No frontend rebuild is required. Deployment itself requires owner approval. Keep idempotency and existing optimization flags unchanged.

## Delivery and check-in safety

Exercise recordings have a stable random upload ID independent of telemetry. Repeats use the existing UUIDv5 idempotency path, never a new recording identity. No hedged requests. This relies on the existing production account lease and upload lock to avoid duplicate STT during concurrent requests; primary-key uniqueness alone prevents duplicate rows but cannot prevent provider calls before insertion.

Check-ins have no upload identity on the server. A changed revision or pending transcription NEVER proves that this recording arrived, even if the revision increased by exactly one. After any ambiguous send, read the checkpoint with a bounded GET. Retry only with the ORIGINAL revision when status and phase are unchanged, revision is exactly unchanged, and pending evidence equals the pre-send evidence. Never replace the revision with a newer one and never overwrite ambiguous pending evidence. If the checkpoint changed, or the read fails, stop in recovery with the recording retained. An explicit Send again reconciles first and obeys the same rule. A stale same-revision send is rejected before STT. Do not automatically assess another tab's pending answer as if it belonged to this recording. A stronger check-in replay guarantee requires a separate server upload-identity design.

## Recovery UI and lifecycle

Sending and retrying notices are text only. Failure says receipt could not be confirmed; a retained recording is available only while this page stays open. Send again reuses that recording without replaying a question. Record again requires an explicit learner action; the prompt is shown and listening begins. Replay question is a separate explicit action using existing replay audio, without regenerating TTS. Network failure feedback never calls a provider.

Retain audio in memory on ambiguous failure. Clear on success, explicit discard/re-record, definitive audio rejection, account change or leaving the lesson. Page reload loses audio: a bounded session marker contains no audio/transcript/token and cannot authorize a resend. Reconcile resumed sessions before normal continuation, and explain uncertainty without claiming non-delivery. Audio-first transcript gates remain on the server; only the question/cue may be displayed.

## Verification and rollout

Deterministic tests cover deadlines in every stage, cancellation with ignored abort, bounded sends, hidden/background pages, stable IDs without diagnostics, failed policy reads, late results, changed revisions and pending evidence. Server tests pin stale and concurrent check-in rejection, exercise replay, no state/provider effects from policy GET, and disabled behavior. Playwright Chromium plus targeted WebKit tests intercept providers/uploads and assert separate recovery actions, retained IDs and no question/TTS request during recovery. These tests establish plumbing, not real Safari network behavior or evaluator accuracy.

After all three PRs pass CI: owner approves merges; deploy dormant with policy OFF; verify health/flags; owner approves a controlled activation and provider-backed iPhone testing. Test Wi-Fi/cellular, background/foreground, reload, airplane mode, and slow links. Measure recovered sends, duplicate attempts/STT, successful-upload latency and unnecessary TTS. Further activation requires review of those results. No automatic activation is part of these PRs.

## Diagnostic limits

A missing Render process trace narrows the failure but does not prove which network leg lost a request. Correlate browser timing with Vercel request logs for the historical failure windows before proposing transport experiments. XHR progress, direct-origin tests and IndexedDB remain out of scope.
