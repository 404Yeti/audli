# AUD-21 natural opening and perceived latency

## Root causes

`app/lesson.py` owns the welcome and transition templates. The former welcome
added “It’s good to listen together”; the application appended an ears-warmed-up
slogan to every check-in. Its reciprocal guard required readiness/thanks wording
and overwrote otherwise natural replies. The OpenAI prompt reinforced “here and
ready to listen.” The previous human-experience filter also rejected ordinary
“doing well” greetings. These application/prompt rules explain the scripted tone;
there is no evidence that lesson advancement strips reciprocal replies.

The whole reliably recognized check-in reaches the model for richer answers.
Routine complete statuses bypass generation. WELCOME does not extract a profile
or evaluate comprehension. REVIEW extracts interests separately. The server
advances through `/heard` only after audible playback completes.

## Changes and examples

- Welcome is short and direct: “Hey Robbie! How’s your day going?”
- Routine positive reply with reciprocity: “Good to hear. Doing well, thanks for asking!”
- Routine tired reply: “Sounds like a tough day. We’ll take it easy.”
- TRANSITION: “Alright, I’ve got something for you to listen to. We’ll focus on …”
- Rich check-ins retain generated context acknowledgment. Common natural reciprocal
  variants are retained rather than rewritten into readiness language.

These are code examples, not observed live-provider transcripts. Routine responses
are deterministic and vary by recognized status; richer answers are generated.
The check-in validator now owns short statement/sentence bounds and safety rules,
not terminal-assessment formatting. Ordinary social conventions are permitted;
claims about human emotions, meals, family, sleep or personal events are rejected
by the existing lexical safety check and prohibited in the prompt. The lexical
check cannot prove semantic safety for every possible output.

Removed the repeated appended transition; the existing authoritative TRANSITION
phase handles that act. Authentication, persistence, revisions, retry/recovery,
transcript gating, coaching overlap and 1.8-second silence detection are unchanged.

## Thinking to Speaking trace

Thinking covers post-recording upload/STT, saved pending attempt, answer processing,
saved answer, audio request, provider TTS, audio validation/sanitization, cache write,
complete body download and browser playback start. UI changes to Speaking when
`play()` resolves; privacy-safe latency telemetry uses `playing`, still an estimate
of audible onset. No artificial UI delay was found.

Cold TTS requires completed response text: `OpenAIProvider.speak` passes it to
`speech.create` and returns `result.content`. `lesson_api.speech` validates the full
result before returning a normal audio Response. `lessonSpeech` awaits the complete
body Blob before creating the playback URL. Warm owner/text-keyed cache hits skip
provider synthesis, sanitization and cache writes, but still await HTTP/body and
playback. Changed wording naturally creates a new cold cache entry; prior text
cannot replay for the new full-text key. Existing tests verify repeated/resumed
opening speech invokes TTS only once.

Exercise coaching prepares during optional acknowledgment playback; playback stays
serial. Opening check-ins do not use that acknowledgment wrapper. Thus an exercise
acknowledgment can occupy the speaker after coaching becomes ready; opening latency
is not caused by waiting for that cue. `/heard` round trips between opening acts are
required persisted checkpoints, not extra model calls. No additional latency
architecture was implemented here: shorter utterances and routine generation bypass
are structural improvements, without measured live-provider millisecond savings.

## Measurements and ranked next steps

The latest local real-microphone test passed opening naturalness acceptance, as
confirmed by the user. Earlier tests reported a noticeable response delay.
No timestamped representative dataset was supplied; p50/p90, cold/warm live-provider
budgets and before/after audible latency remain unavailable. Earlier synthetic cache
benchmarks and virtual-time browser stages do not establish conversational latency.

1. Collect at least 30 reliable turns per equivalent workload/version, separating
   routine/rich opening, REVIEW, exercise-answer and transition, and cold/warm cache.
   Use numeric E/A/M observer markers in `AUD-21-MICROPHONE-ACCEPTANCE.md`; browser
   `upload_transcription`, `checkin_response`, `tts_readiness`, `playback_start` and
   server spans distinguish network/application work from provider processing.
   `checkin_response` is inclusive request time, not model generation alone.
2. If cold TTS dominates, review bounded next-prompt preparation after persisted
   decisions. Medium complexity: account/revision ownership, cancellation, duplicate
   cold-cache misses, cost and recovery must be covered before implementation.
3. Consider streaming only if body-delivery time materially dominates. High complexity:
   validated audio cannot currently be released before full sanitization; bearer-auth
   requests cannot simply become unauthenticated media URLs. Streaming requires a
   design for partial failure, cancellation, serial playback, cache completion and
   checkpoint recovery. Faster first network bytes need not mean faster audible audio.

Do not log/export learner audio, transcripts, credentials, profile contents or HAR.
Do not shorten the silence detector; a 1.5-second legitimate pause was previously
clipped by a 1.2-second threshold. The ≤5-second target remains unverified.

## Verification

Full SQLite/PostgreSQL backend: 508 passed. Final focused lifecycle/provider:
135 passed; final check-in safety boundary: 12 passed. Frontend helpers: 51 passed.
Chromium: 81 passed, 1 skipped. ESLint and production build (including TypeScript)
passed. The full run preceded the final broadened reciprocal variants and blank-reply
boundary, which are covered by the focused reruns. Regression coverage
includes social intent/context, response length, absence of slogans, varied generated
reciprocity preserved without mandatory readiness wording, invented-life rejection,
rich-answer routing, progression, provider failure, interrupted revision/recovery,
serial opening playback, stale audio cancellation and repeated TTS reuse. These
controlled tests protect behavior. The user subsequently confirmed successful local
real-microphone acceptance of the natural opening and reciprocal responses.
Previously persisted replies intentionally retain their original wording on recovery;
use a fresh lesson for tone acceptance. AUD-21 remains In Progress pending
representative latency measurements. AUD-23 is unchanged; authenticated production
acceptance remains pending. These changes are authorized for a local commit only,
without a push or deployment.
