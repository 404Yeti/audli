# AUD-68 — the under-three-word uncertainty rule: investigation and proposal

Status: **investigation + characterization tests only. No gate was changed.** Any behaviour change needs owner approval
and the tests in §6. Linear: AUD-68 (Phase 0). Naming: "AUD-68" here means this Phase 0 issue, not the earlier
lifecycle PR #50 (see §8).

## 1. What the rule does today (code facts, `origin/main` @ `289e093`)
* `OpenAIProvider.transcribe` (`app/services/openai_provider.py`) appends `Too little recognized speech to evaluate reliably.`
  when the transcript has fewer than 3 whitespace-separated words, **regardless of confidence**.
* `reliable_recognition` (`app/recognition.py`) is true only if there is text, a confidence ≥ the minimum (0.65) **and no
  uncertainty entry at all**. So a correct, confidently transcribed 2-word reply is "unreliable".
* The web mirror (`reliableRecognition`, `web/lib/hands-free.ts`) applies the same "no uncertainty" test.
* Two paths already carve out an exception, by string equality on the warning text:
  `reliable_choice` (`app/homework_voice.py`) and the choice branch in `app/homework_review.py`. Both accept a short reply only if
  the **whole utterance parses as a known closed choice**, confidence passes, and no other warning exists.
  The warning string is duplicated (`SHORT_ASSESSMENT_WARNING` and the provider's literal); nothing but a test ties them.

| Context | Server gate | Client gate | Short reply today |
|---|---|---|---|
| Comprehension summary / follow-up (hands-free assess) | `reliable_recognition` (`conversation_api.assess`) | `page.tsx:201` | rejected → "I didn't quite catch that" (≤2 retries, then error) |
| Lesson WELCOME check-in / REVIEW topic reply | `reliable_recognition` (`lesson_api.answer`) | `page.tsx:288` | rejected, same retry loop. I found no typed fallback on this screen. |
| Planner topic check-in | `reliable_recognition` (`planner_api.answer`) | `adaptive-plan.tsx:51` | rejected; "Keep my topics" button and typed field exist |
| Onboarding answers | `reliable_recognition` (`onboarding_api`) | `onboarding.tsx:141,151` | rejected; typed-name fallback exists |
| Homework yes/no/topic decision | `reliable_choice` | n/a | **accepted** for known choices |
| Homework review | `reliable_recognition` + choice exception | n/a | accepted for known choices |

## 2. Evidence (real provider, synthetic US-English voice; `docs/AUD-68-PROVIDER-BASELINE.json`)
Two small controlled runs (not accented speech, not learners; `gpt-4o-mini-transcribe`, min confidence 0.65).

| Measure | Result |
|---|---|
| Short-reply set (10 replies; 6 have ≤2 words) | 6/10 flagged `too_short` in both runs; gate pass rate 30% (run 1) and 40% (run 2) |
| Those replies' transcription accuracy | essentially exact (category WER ≈ 0.0–0.1 on the short set) |
| Read-aloud, grammar-error and number prompts | gate pass rate 100% |
| Non-speech clips (silence, quiet noise, broadband noise, 1 kHz tone; n=4, synthetic) | **0/4 produced any text**; 0 false accepts |
| Run-to-run variance | one borderline reply flipped on confidence between runs |

Reading: the rule rejects **correct** short answers (false-unreliable) in these runs. The hallucination risk that such a rule
typically defends against was **not observed** here, but n=4 synthetic clips cannot rule it out: real rooms, hum, breath and
phone-handling noise were not tested. Vietnamese-accented short replies were not tested (no audio yet).
Not verified: whether the single "false reliable" number-prompt case is only numeral formatting ("9:15" vs "nine fifteen").

## 3. Diagnosis
The rule mixes two different questions in one list of strings:
1. **Recognition reliability** — is this text what the learner said? (empty text, missing/low confidence, provider warnings.)
2. **Content sufficiency** — is this enough for the *purpose*? (a comprehension summary needs substance; "Yes please." is a complete answer to a yes/no.)

Sufficiency depends on context, so it belongs to the consumer, not the transcriber. For comprehension evidence the evaluator
already models it: `insufficient_evidence` is *unknown, not zero* (AGENTS.md) and triggers at most two follow-ups.

## 4. Proposal (not implemented)

**Step A — no behaviour change (refactor, safe to approve first).**
* Centralize the warning texts as constants in `app/recognition.py`; import them in the provider and `homework_voice`.
* Add `recognition_issues(transcription, minimum) -> set[{EMPTY, NO_CONFIDENCE, LOW_CONFIDENCE, TOO_SHORT, OTHER}]` and
  re-express `reliable_recognition` as `not issues`. A table-driven test proves identical results for every combination.
* Emit a numeric counter per `(context, issue)` through the Phase 0 aggregator so real rejection rates become visible.

**Step B — behaviour change (needs approval + tests + data).** Add an explicit `purpose` argument; default stays strict.

| Purpose | Contexts | Rule |
|---|---|---|
| `EVIDENCE` (default) | comprehension summary/follow-ups | **unchanged**: TOO_SHORT still blocks |
| `CLOSED_INTENT` | WELCOME check-in, planner check-in, homework | TOO_SHORT allowed **only if** the whole utterance matches that context's closed-intent parser (generalizes `reliable_choice`; check-in can reuse `routine_checkin_reply`), confidence passes, no other issue |
| `TOPIC_NAME` | REVIEW/planner topic words ("Football.") | candidate: allow 1–2 words at a **higher** confidence (e.g. ≥ 0.85) with no other issue, and echo the topic in the spoken acknowledgment so a mishearing is audible. Only if Vietnamese-accent data supports it. |

Why closed-intent first: it adds **no new hallucination exposure** beyond what homework already accepts, because an unexpected
short phrase still fails. Confidence-only acceptance is broader and should wait for real-noise data.

Client: `reliableRecognition(value, minimum, purpose)` mirrors the server. A single shared JSON fixture of
`(text, confidence, uncertainty, purpose) → expected` is run by both the Python and Node suites so they cannot drift.

Unchanged by design: the 0.65 confidence threshold; null confidence ⇒ unreliable; every provider warning other than TOO_SHORT;
persisted `pending` transcripts keep all reasons for audit; unreliable attempts still cannot change learner state.

## 5. Risks
* A short noise-hallucination ("Thank you.", "you") accepted as a check-in answer. Mitigation: closed-intent grammar first; measure on real
  noise before any confidence-only path.
* Client/server drift. Mitigation: shared fixture.
* Changing check-in acceptance changes the lesson experience: needs the iPhone checklist.
* Accent effects: shorter utterances give the recognizer less context; Vietnamese final-consonant reduction could lower confidence on
  1–2 word answers. Unmeasured.

## 6. Tests required before any behaviour change
1. Table-driven equivalence for Step A (all combinations of text/confidence/warnings).
2. Per-purpose acceptance/rejection matrix incl. boundaries 0.64/0.65/0.66 and 0.84/0.85/0.86 where used; unknown short phrases rejected; mixed
   decisions ("yes but no") rejected.
3. Non-speech corpus: silence, room noise, keyboard, breath, TV/background speech, phone handling (real recordings, consented) with an agreed
   false-accept ceiling.
4. Vietnamese-accent short-reply set via `scripts/evaluate_stt.py` (category `S`), reporting pass rate before/after.
5. Shared client/server fixture; Playwright WELCOME flow with a 2-word answer; retry-limit behaviour unchanged for unknown phrases.
6. Characterization tests already added: `tests/test_short_reply_rule.py` (current behaviour, incl. homework exception and the string coupling).

## 7. Decision requested
Approve **Step A** (pure refactor + counters)? Step B should wait for the consented accent audio and a real-noise set.

## 8. Naming note
Linear **AUD-68** = this Phase 0 diagnostics work; **AUD-69** = Phase 1 durable state. The earlier lifecycle fix (PR #50,
`docs/AUD-68-LESSON-LIFECYCLE.md`, branch `fix/AUD-68-lesson-lifecycle`) carried the same number in repository history. Those
records are left as written; new documents say "AUD-68 Phase 0" or cite the PR number.
