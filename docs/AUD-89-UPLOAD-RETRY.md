# AUD-89 P2 — Bounded recording-upload retry and clear failure states

Status: PR A (policy endpoint, kill switch, characterization tests) — PR B (sender, retention, check-in reconciliation) — PR C (learner-facing states, Playwright and WebKit). Nothing is enabled by merging; the feature is switched on only by `AUDLI_UPLOAD_RETRY=true` on the API.

## Evidence (production, 2026-10-25, two lessons, 20 sends)
* 4 sends failed, all the same way: the browser's `fetch` rejected with `TypeError: Load failed` after **12.0–12.2 s**, and **no server line exists** (not even a disconnect line from the P0 `Upload trace`), so the request never reached the API. The page was visible, the browser reported online, idle time was 2.6–7.9 s, and the failed body (112 KB) was smaller than most that succeeded.
* In the opening check-in an immediate resend of the same recording failed again; a fresh recording after the lesson was resumed succeeded.
* The 16 sends that arrived: client time 4.3–6.1 s, 0.2–0.6 s outside the server's own work, Vercel→Render 0.2–0.5 s, bodies 97–250 KB (about 26 KB/s of speech, so a 60 s answer is about 1.6 MB).
* Today's failure UX: "Retry conversation" and "Record again" both replay the greeting or question before listening, which costs a text-to-speech call each time and tells the learner nothing; "Record again" also discards the saved recording.

## Policy (server-delivered, fail-closed)
`GET /api/upload-policy` (authenticated, read-only, no lease) returns `{"enabled": false}` unless `AUDLI_UPLOAD_RETRY` is a clear `true`. Anything else, a malformed or out-of-range `AUDLI_UPLOAD_RETRY_POLICY` (optional JSON overrides, strict types, unknown keys rejected), or a failing/odd answer in the browser, means **retries stay off** and behaviour is exactly today's. Kill switch: unset the variable (about 2 minutes, no browser rebuild).

Defaults: `base_ms 8000`, `bytes_per_second 64000`, `min_ms 9000`, `max_ms 30000`, `growth [1, 1.25, 1.5]`, `max_sends 3`, `deadline_ms 50000`, `backoff_ms [300, 900]`, `hidden_grace_sends 1`, `slow_hint_ms 3000`.

### Per-send timeout
`timeout(send i) = clamp(base_ms + bytes / bytes_per_second, min_ms, max_ms) × growth[i]`, counted in **visible** time. A fixed 8–9 s would be wrong: with 5 s of server work, a 30 s answer on a 1 Mbps link needs about 11.8 s and a 10 s answer on 500 kbps about 9.7 s.

| Recording | Bytes | Timeout (send 1) | Typical total on 5 Mbps | 1 Mbps | 500 kbps | 250 kbps |
|---|---|---|---|---|---|---|
| 5 s | 132 KB | 10.1 s | 5.7 s | 6.6 s | 7.6 s | 9.7 s |
| 10 s | 263 KB | 12.1 s | 5.9 s | 7.6 s | 9.7 s | 13.9 (late) |
| 30 s | 789 KB | 20.3 s | 6.8 s | 11.8 s | 18.1 s | 30.7 (late) |
| 60 s | 1.58 MB | 30.0 s | 8.0 s | 18.1 s | 30.7 (late) | 56 (late) |

Three sends of a typical 130 KB answer take 10.1 + 12.6 + 15.1 s = 37.7 s (+1.2 s back-off) of the 50 s deadline. Links at or below about 250 kbps will time out on long answers; for them the longer later sends and the explicit **Send again** are the safety net. For typical recordings the first timeout is only about 2 s under the observed 12 s failure, so speed gains depend on the retry succeeding, which we will measure; the firmer value is that the recording is never lost and the learner is told.

## Exercise uploads
Every send of one recording carries the same `X-Audli-Upload-Id` (it follows the recording, P0) and the server's idempotency (P1) turns a repeat into a replay of the first attempt: no second attempt, no second transcription. So an ambiguous failure is resolved by simply sending again.

## Check-in uploads (no server idempotency)
The revision check is the arbiter: `POST /api/lessons/{id}/attempts` refuses a stale revision with 409 **before** any transcription, and concurrent duplicates of one revision commit once (characterization tests in this PR). So a blind resend cannot apply twice; reconciliation exists so the learner is not asked to repeat or shown an error when the first send had in fact committed. After an ambiguous failure (network error, timeout) the browser reads `GET /api/lessons/{id}` and decides from the revision `R` it sent and the `pending` it held before sending (`P0`):

| Server state (C) | Decision |
|---|---|
| active, same phase, `C.revision == R + 1` **exactly**, `C.pending` present and **different from `P0`** | our upload committed: adopt `C`; no resend, no new recording |
| active, same phase, anything else (revision unchanged, a jump larger than one, `pending` null, or `pending` identical to `P0`) | not proven delivered: resend with `C.revision` (a second transcription is the worst case; state stays correct) |
| status or phase moved, or a different lesson state | adopt the server checkpoint and continue from it |
| read failed | resend with the revision already held; the server's 409 corrects a stale one, then reconcile again |

A revision change alone never proves delivery (end and resume bump it with no upload), and a pending transcription alone never does either (it can belong to an earlier recording, and a resend overwrites it). Adopting a `pending` still runs the normal reliability check, so an unreliable transcription gets the existing "could you say it again" prompt.

## Learner-facing states (PR C; text only, announced politely, focus moves to the failure card)
| State | Text | Controls |
|---|---|---|
| Sending (after `slow_hint_ms`) | "Sending your answer…" | none |
| Retrying | "Weak connection, trying again (2 of 3)…" | none |
| Failed | "I didn't receive your answer. Your recording is saved on this device." | **Send again**, **Record again** |
| After Record again | "Okay, let's try that again." followed by the question text | listening starts; "Hear the question again" is an explicit button |
| Resumed after a reload (recording gone) | "I didn't receive your last answer." before anything is replayed | the normal Start/Resume tap |

* **Send again** resends the saved recording; it does **not** replay the greeting or question.
* **Record again** shows the question as text and starts listening; the audio is replayed only if the learner asks. No retry feedback is spoken, so there are no extra text-to-speech calls, and the replays that used to regenerate audio on every retry disappear.
* The recording stays in memory only (no IndexedDB). It is cleared only on success, on a definitive rejection (413/415/422), or by an explicit **Record again**.

## Safari, backgrounding, resume
* Timers run on visible time. A send killed while the page was hidden gets `hidden_grace_sends` extra sends without spending the budget.
* On `pagehide`/`pageshow` (including the back/forward cache) the page returns to the Failed state with the recording still held.
* A cancelled operation (leaving the lesson) does not retry. A small `sessionStorage` marker (no audio, no text) records "an upload was in flight" so a reload can explain itself.

## Privacy and provider calls
Telemetry is the P0 delivery record (numbers and enums only) plus at most a small enum for the reason a retry happened. No audio, transcript, URL or identifier is stored or logged. Reconciliation reads call no provider; there is no hedged parallel send; retry feedback is text.

## Rollout, rollback, acceptance
1. Merge A, B, C with the flag off (no behaviour change). Verify health shows `upload_retry: false`.
2. Preview deployment on Vercel, one iPhone lesson with the flag on against a non-production API, or in production with the flag on for the owner's lesson only.
3. Rollback: unset `AUDLI_UPLOAD_RETRY` (or set it to anything but `true`). A client defect is rolled back by promoting the previous Vercel deployment. No data repair is ever needed.

Acceptance (measured from the P0 delivery records and the server logs): no recording lost without the learner being told; no greeting or question request between a delivery failure and a learner action; one attempt and one transcription per recording (the documented check-in worst case aside); successful-upload p50 within 5 % of today; report the share of first-send failures recovered by the retry. If that share is low, the held experiments (XHR upload progress, hedged sends, direct API origin) are reconsidered separately.

## Finding where the 12 s failures happen (Vercel request logs)
Vercel dashboard → project `audli` → Observability (Edge Requests) or Logs; host `app.audli.ai`, method POST, path containing `/attempts`.
* 2026-10-25 check-in: `/api/lessons/6962cadd-3d43-48ee-825c-658daab45bdf/attempts` at 13:48:09–13:48:22 and about 13:48:34–13:48:46 UTC.
* Earlier lesson: `/api/exercises/12a68291-ce6e-45f3-9429-77fbfa21d807/attempts` at 06:59:54–07:00:06 and 07:01:05–07:01:17 UTC.

| What Vercel shows | Meaning |
|---|---|
| no entry | the request never reached Vercel: the phone or its network |
| an entry closed at about 12 s with less data in than the 112 KB body | the upload stalled between the phone and Vercel |
| 502/504 at about 12 s with the whole body in | the Vercel→Render leg stalled |
| 200 after more than 12 s | the browser gave up first |
