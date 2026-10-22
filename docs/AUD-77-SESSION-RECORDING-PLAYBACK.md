# AUD-77 — Session-only original recording playback (investigation)

Status: investigation only. **Nothing here is implemented**; Phase 1 ships the saved transcript ("What Audli heard") and leaves the lesson loop (`web/app/page.tsx`, AUD-73) untouched.

## Findings (source inspection of origin/main 4603ed5; not runtime-verified)

- The recording `Blob` exists only in the browser. `captureAnswer` (`web/app/page.tsx`) obtains it from `voice.listen`, uploads it, then sets `retained.current = null`. The server validates and discards it after transcription. No raw audio is stored or logged.
- The `Blob` and the server `attempt.id` are both in scope at one point: right after `sendRecording(...)` resolves in `captureAnswer`. Review's saved answers are keyed by `attempt_id` (added in this PR), so a lookup `attempt_id -> Blob` is possible with no new server data.
- Retry turns (unreliable recognition) create new attempts; only attempts that became accepted turns appear in Review, so stored blobs for rejected attempts would simply never be looked up.

## Proposed safe design (Phase 1b, needs approval before wiring into the lesson loop)

1. A small in-memory module (`web/lib/session-recordings.ts`), no `localStorage`/IndexedDB/Cache API, no network, no object URL until the learner presses play.
2. Key `account.scope` + `attempt_id`; entries from another scope are never returned, and the store is cleared on sign-out, account change and tab close (memory only).
3. Bounded: keep the most recent N (suggest 6) recordings and at most ~24 MB total; evict oldest first. Upload already caps one recording at 12 MB.
4. Review shows "Play my recording" only when the Blob is present, labeled "This session only. Audli doesn't keep your recordings." Otherwise nothing is shown (no error, no server request).
5. Create the object URL on press, revoke on pause/end/unmount; stop playback when leaving Review (Review already tears down its audio on navigation).
6. One call site in `captureAnswer` after a successful upload (`remember(scope, attempt.id, blob)`). It does not alter phases, callbacks, retries or the completion flow.

## Risks and open questions

- **AUD-73 proximity:** the single hook sits in the lesson loop. It must be a pure side effect that cannot throw into the loop (wrap in try/catch) and be covered by the existing ending/completion specs.
- **Shared device:** until sign-out or tab close, a recording stays in memory; mitigated by clearing on sign-out and not persisting.
- **Playback format:** blobs are whatever `MediaRecorder` produced (WebM/Opus on Chromium and Firefox, MP4 on Safari). Playback in the same browser that recorded it is expected to work, but this must be checked on a real iPhone Safari before shipping; I did not test devices.
- **Reload/new tab loses the recording** by design; the UI must say so.
- **Memory:** bounded as above; confirm on low-memory phones.
- **Not a server retention decision:** this design needs no retention, deletion or AUD-34/AUD-36 work.
