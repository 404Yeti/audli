# AUD-91 — short replies to the active closed question

The transcriber still attaches its under-three-word uncertainty warning. Previously
this blocked even a confident “Yes, please” before the accent-offer interpreter,
although the tutor explicitly asks yes/no questions. Homework already has a narrow
short-choice exception; comprehension evidence must stay strict.

`app/lesson_recognition.py` now computes an `accepted` / `uncertain` verdict for
the pending check-in. Only a full affirmative/refusal matching the current
unresolved WELCOME offer or explicit REVIEW confirmation may ignore **only** the
length warning. The target must match the server's actual question (including a
bounded clarification about a new target), consent must still
be ungranted, and the lesson must be active within its original ten-minute wall
window (future timestamps fail closed). Resolved/missing/expired/consumed grants,
open topic questions, mixed decisions and all other uncertainty stay blocked.

This is recognition sufficiency, not authorization to select a voice. Existing
server intent, availability, consent and persistence paths still apply. No
provider call or database read is added to compute the verdict. Raw transcription
and warning list are preserved; nothing new is stored in lesson JSON or schema.

The public checkpoint includes optional `pending_recognition`; the browser uses
that server verdict. Missing fields from an older backend use the existing strict
gate. The `/answer` endpoint accepts only a revision and recomputes under the
current checkpoint; posting a forged verdict is rejected. Stale revisions fail
before state mutation. Resume/read responses also recompute, so an expired verdict
cannot authorize a later action. The handler also rechecks after its asynchronous
availability read so crossing the deadline cannot consume short consent.

No recording duration/VAD, confidence threshold, comprehension gate, upload retry
setting or transport logic changes. Bare “Okay” is not a topic or global consent.
No added audio/transcript logs, provider calls, schema migration or feature flag.

## Regression coverage

* Python: valid synthetic WAV -> decoder -> actual OpenAI transcription
  classification with mocked SDK -> saved pending warning/verdict -> intent and
  consent. WELCOME/REVIEW affirmatives/refusal, duplicate actions, stale/missing/
  expired offers, expiry during availability, concurrent checkpoint changes,
  retargeted/spontaneous clarification, empty/missing/low confidence, ambiguity, other warnings,
  .64/.65/.66 boundary and configured thresholds, unchanged comprehension state.
* Chromium and iPhone WebKit: synthetic microphone/MediaRecorder bytes go through
  actual browser capture/finalization/VAD/upload and real local API/SQLite. Only
  microphone/SDK/speech outputs are prescribed. Short choices progress once
  without recognition retries; low confidence, ambiguity and absent offer stay
  unaccepted. No interception of upload or answer responses. The test stops at
  the completed check-in before unrelated exercise generation.
* Existing short-reply, accent consent/intent, homework, recording and CI suites
  guard unaffected behavior. Mocked tests establish plumbing and safety, not STT
  accuracy for real accents, noise or native phone microphone performance.

Run the focused browser suite using the existing offline CI environment:

```sh
AUDLI_SHORT_CHOICE_TEST=1 npx playwright test --config=short-choice.playwright.config.ts
```

The harness is isolated under `tests/`, launched only by this config, with
fixture-control endpoints absent from the production application. CI runs both
browsers inside the already pinned Playwright image. Physical acceptance remains
a separate post-approval task. AUD-89 remains separate and In Review.
