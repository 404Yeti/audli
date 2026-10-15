# AUD-42 microphone investigation

## Scope and evidence

Inspected current main e583a35, not the unrelated work in the shared developer checkout. The beta student's device-specific failure is not reproduced: no device, OS/browser version, permission state or precise symptom was supplied. Automated Chromium media mocks establish application behavior, not real hardware, Safari, Android, Chrome or Edge device certification.

Two new browser regressions were also run against the original main page/lifecycle: both failed (rejected audio never opened a new capture; suspended context never offered interruption recovery). The same cases pass with this fix.

Confirmed application defects:

- A rejected upload (413/415/422: oversized, unsupported, undecodable, short or silent audio) remained in the retained-recording cache. Retry resubmitted the same rejected bytes indefinitely. All three recording flows now invalidate only definitively rejected audio; network/server/transcription failures preserve it.
- Capture watched neither page visibility nor a running AudioContext becoming suspended. A suspended analyser could continue feeding zeros into turn detection while the UI claimed to listen. Capture now finalizes on interruption; available bytes are retained for an explicit retry or replacement, with no automatic assessment. Mere window blur does not terminate capture.
- Stop depended indefinitely on the browser delivering its final stop event. A deterministic missing-event test now verifies bounded cleanup and retry feedback after five seconds. This timeout does not change the 1.8-second end-of-speech threshold.
- Retry explicitly reset the running guard, permitting overlapping continuations. Retry now respects the existing guard.
- Permission/device errors were conflated, and capture-start errors were overwritten with a generic message. Messages now distinguish denied access, missing microphone, busy/unreadable input, interruption and transcription failure.

## Pipeline trace

1. `session.microphonePermission` requests `{audio:true}` on session Start and immediately stops preflight tracks. Onboarding/actual capture acquires its own stream. No persisted device ID, device labels or enumeration: the OS/browser-selected default input is used. Users change inputs in browser/OS controls.
2. `useVoiceLifecycle.listen` pauses tutor playback, acquires an account-bound stream, resumes an AudioContext, connects its analyser and starts `captureRecording`. The UI becomes Listening only after recorder readiness and a live enabled input check.
3. `captureRecording` probes WebM/Opus, MP4, Ogg/Opus; otherwise it uses the browser default. Actual recorder MIME (then chunk MIME) determines the Blob and upload extension. Existing MP4 fallback is retained. There is no forced WebM encoding for Safari.
4. Existing local turn detection samples every 50 ms, requires 300 ms voiced activity, and ends after **1800 ms** silence (minimum two seconds total). No-voice timeout remains 15 seconds; continuous capture cap remains 119 seconds. This is a turn heuristic, not evidence that the learner was silent.
5. Final Blob construction waits for final `dataavailable` before `stop`. Empty/unsupported output fails without upload. Cleanup stops input tracks, disconnects analyser, closes context and detaches callbacks/listeners/timers. Stale operations cannot publish audio across End/unmount/account changes.
6. The hands-free lesson has no learner preview playback; `recordingPreview` is an existing helper, independently covered for URL ownership/revocation. Tutor playback completes before capture; capture pauses its audio element. Replay exists only during tutor speech.
7. Original Blob bytes are submitted with FormData and a MIME-derived filename; multipart boundary headers are browser-owned. Existing operation/running guards and authoritative server pending-attempt checkpoints prevent ordinary duplicate retries and reuse of a saved summary as follow-up audio. This does not introduce a general HTTP idempotency protocol.
8. FastAPI validates content type, 12 MB size, signature, FFmpeg decoding, 2–120 second decoded duration and PCM RMS. It rejects measured near-silence distinctly from capture/transport/STT failures. Temporary decode files and UploadFile handles are deleted/closed. No new retention or database change.
9. Transcription occurs only after validation. A failed transcription returns specific safe feedback without provider payloads; no attempt/evidence or learner adaptation is committed. Retrying can resend the in-memory recording. Transcription uncertainty still follows the existing spoken retry flow and never becomes confirmed zero evidence.
10. Evaluation/follow-up selection, adaptation, accent routing, transcript gates, tutor voice and saved lesson checkpoints are unchanged. An upload response lost after persistence is recovered from the server's pending attempt before recording another turn.

## Recovery and privacy

`Retry conversation` keeps recoverable audio; `Record again` explicitly replaces it. Invalid server-rejected audio is replaced on retry. Captured audio is memory-only and is intentionally released on success, explicit End, account unmount or page reload; this change does not promise reload-surviving recordings. In an interruption, retry sends the captured portion; Record again is preferable if the answer was cut off. Recorder errors and missing finalization cannot establish a valid complete container and report failure instead of submitting arbitrary partial chunks.

Diagnostics remain off in production by default. Build with `NEXT_PUBLIC_AUDLI_RECORDING_DIAGNOSTICS=1` for a limited beta investigation, then remove the flag/rebuild. Logs stay in the browser console; no third-party service or server ingestion was added. Events contain browser token/version, MIME, chunk/Blob byte counts, elapsed capture duration (wall time, not decoded duration), recorder state, interruption, upload status and coarse error category. Do not send raw audio, transcripts, full user agents, device names, account IDs or provider errors to this logger. Browser version parsing is best effort (Safari uses its Version token); it is not a capability assertion. Server validation remains authoritative.

## Regression scenarios

Unit tests cover successful readiness, delayed/missing start, duplicate stop, missing stop event, unexpected stop with final audio, stale callbacks, empty Blob, MP4 MIME fallback, chunk MIME fallback, oversized/unsupported formats, live input checks, preview URL lifecycle, permission categories, and retained-byte behavior for validation/network/STT failures.

Browser tests use mocked media streams/recorders/AudioContexts and HTTP endpoints: permission denial, unavailable input, empty audio, valid capture/upload, unexpected stop, context suspension, hidden page, harmless focus loss, rejected-audio replacement, retained transcription failures, explicit replacement, playback transitions, lost upload response, uncertainty follow-ups and End/auth cancellation. Existing integrated acceptance uses synthetic browser capture, real local HTTP/FFmpeg and local speech, with no paid providers.

Backend coverage includes decoded silent audio, invalid encoding/signatures, size checks, transcription failure followed by same-audio retry, untouched learner state and transcript gating. Full required CI results are recorded on the PR.

## Remaining real-device reproduction gaps

Test physical iPhone Safari and other iPhone browsers, Android Chrome, desktop Chrome/Edge with built-in and Bluetooth/USB inputs. Include permission revoke/regrant, disconnected/default input changes, another app holding the microphone, lock screen/app switch, incoming calls, OS interruptions and real MP4/WebM playback/decoding. Synthetic tests cannot verify OS audio routing or microphone hardware sensitivity.

For the student's report, collect only: device model, OS version, browser name/version, built-in/headset/Bluetooth input, whether access was granted, whether Listening appeared, whether it stopped automatically, displayed error text, whether tutor audio played, whether switching apps/locking occurred, and whether retry worked. With diagnostics enabled, collect MIME, byte size, duration, transition/error/status metadata. Do not request their recording or answer transcript.
