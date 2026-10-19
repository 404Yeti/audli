# AUD-71 — recognition issues and gate counters (Step A, no behavior change)

Follows `AUD-68-SHORT-REPLY-RULE.md` §4 Step A. **No learner-facing acceptance rule changed.**

## What changed
* `app/recognition.py` is the single home of the three transcriber warning texts, `MINIMUM_REPLY_WORDS`, a `RecognitionIssue` enum
  (`empty`, `no_confidence`, `low_confidence`, `too_short`, `other_warning`) and `recognition_issues()`.
  `reliable_recognition()` is now `not recognition_issues(...)` and is proven identical to the old implementation.
* `OpenAIProvider.transcribe` and `homework_voice` import those constants (strings are byte-identical; `SHORT_ASSESSMENT_WARNING` remains as an alias).
* Six call sites use `check_recognition(settings, context, …)` / `observe_recognition(…)`; the boolean they return is unchanged.
* Counters (only when `AUDLI_LATENCY_AGGREGATES=true`), emitted in the existing `Latency summary` line under `counters`:
  `recognition.<context>.accepted|rejected` and `recognition.<context>.issue.<issue>`, for contexts `exercise.assess`, `lesson.answer`,
  `planner.checkin`, `onboarding.answer`, `homework.review`, `homework.decision`. Names are fixed code constants (≤128 counters; others collapse
  to `other`); no text, IDs or confidence values.
  Homework counters record the **final** decision (including the closed-choice exception), so `issue.too_short` next to `accepted` shows the exception working.

## How behavior is frozen
* `tests/test_recognition_gate.py`: every combination of text × confidence × warnings × minimum equals a verbatim copy of the old gate.
* `tests/fixtures/recognition_gate.json` (300 cases, expectations produced by the OLD code) is checked by Python **and** by the web client's
  `reliableRecognition` (`tests/hands-free.test.mjs`). Any future change to a row is, by definition, a behavior change needing approval.
* Existing HTTP tests (hands-free assess, onboarding, homework) pass unchanged; a new HTTP test shows an uncertain attempt is still a 422 with no state change and is counted.
* Mutation check: letting the gate tolerate `too_short` fails 9 tests.

## What this enables
After telemetry is on, the real share of rejected replies by reason and context (e.g. `lesson.answer.issue.too_short`) becomes visible, which is
the data Step B needs. Step B (context-aware acceptance) is **not** included and still requires owner approval, consented accent audio and a real-noise set.
