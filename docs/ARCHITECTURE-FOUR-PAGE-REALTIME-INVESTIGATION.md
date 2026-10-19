# Audli — Four-page architecture & Realtime voice investigation

Status: **research only, awaiting approval.** Nothing was implemented, committed, pushed, deployed or migrated.
Date: 2026-10-18. Author: Claude Code (read-only investigation).

## 0. Evidence base and caveats (read first)

* **The checked-out branch is not production.** `feat/AUD-25-optional-tutor-tts` (HEAD `2467176`) predates AUD-64…AUD-68: it has no `lesson-phase.ts`, no homework UI, no Rive and no celebration. All findings below are from **`origin/main` @ `289e093`** (AUD-68, PR #50), exported read-only with `git archive` to a scratch directory. Your dirty working tree was not touched. `file:line` references are to that commit.
* I did **not** run the app, the Playwright suites (WebKit cannot launch in this WSL, per AUD-68 doc) or any production/Render/Linear query. No production metrics were available; latency numbers are from the repo's own AUD-21 documents and are small development-mode samples.
* Realtime API facts come from OpenAI's current developer docs fetched today; where a fact was not in the docs it is labelled **unverified**. Cost figures are **estimates to be measured**, not quotes.
* Linear: not updated (this is a read-only investigation; AGENTS.md says to track substantial work — a new issue should be created if you approve Phase 1).

## 1. Current-state architecture

### 1.1 It is already one route

`web/app/` contains only `page.tsx` (plus auth/recovery pages). The "screens" are conditional renders inside one client component tree:

```
/ (AuthGate → ApplicationFlow)
 ├─ destination==='onboarding' → SpokenOnboarding           (components/onboarding.tsx, 270 lines, own useVoiceLifecycle)
 └─ Product (app/page.tsx:80-440, 361 lines, 17 useState + 13 useRef)
     ├─ !active:  Home | Review | Plan | Settings            (BottomNavigation → local state, not routes)
     ├─ active:   SessionShell
     │    ├─ TOPIC_SETUP      → LessonTopicCheckin
     │    ├─ TRANSITION       → HomeworkReview (previous homework)
     │    └─ WELCOME…EXERCISE…CLOSING  (voice only, mascot + status)
     └─ completed (after COMPLETED):
          ├─ HOMEWORK  → SessionShell + ListeningHomework(inline)
          ├─ SUCCESS   → LessonCelebration → RiveMascot
          └─ STATS     → SessionShell(completed) + summary + ListeningHomework(finished) + Return Home
```

So "four pages" would not remove routes. It would regroup **nine phases that already exist** (`HOME, TOPIC_SETUP, SMALL_TALK, HOMEWORK_PREFLIGHT, LESSON, FEEDBACK, HOMEWORK, SUCCESS, STATS`, `web/lib/lesson-phase.ts:12`).

### 1.2 State owners (the real complexity)

| # | Owner | What it holds | Where | Persistence |
|---|---|---|---|---|
| 1 | Server lesson checkpoint | `WELCOME, WELCOME_ACK, REVIEW, REVIEW_ACK, TRANSITION, EXERCISE, CLOSING, COMPLETED`, `revision` (CAS) | `app/lesson.py:22`, `lesson_lifecycles.data` JSON, `save_lesson` CAS (`storage/repository.py:495`) | durable |
| 2 | Server per-exercise conversation | `LISTENING … READY_FOR_NEXT`, pending attempt, follow-ups | `app/conversation_api.py` | durable |
| 3 | Server homework assignment | `offered/assigned/skipped/unavailable/…` | `app/homework_api.py` (12 routes) | durable |
| 4 | Server planner check-in | milestone due/offered/answered | `app/planner_api.py` | durable (but "offered" is claimed at offer time, see AUD-68 doc §Root cause 2) |
| 5 | Browser phase reducer | the 9-phase `PhaseState` | `lesson-phase.ts` | **memory only** (`useRef`+`useState` mirror, `page.tsx:89-95`) |
| 6 | Browser post-completion hints | completed lesson id; "homework resolved" id | `sessionStorage` `audli-session:<scope>` and `…:homework` (`page.tsx:135,383`) | **tab-scoped** |
| 7 | Browser topic-offered milestone | `localStorage audli-topic-offered:<scope>` | `page.tsx:312` | per-browser |
| 8 | Browser conversation flow | `conversationReducer` phases | `lib/conversation.ts` | memory |
| 9 | Browser "ended" intent | `localStorage audli-ended:lesson:<id>` | `lib/conversation-intent.ts` | per-browser |
| 10 | Voice lifetime | `LessonLifetime`/`LessonOperation` abort tree, one shared `<audio>`, recorder, AudioContext | `lib/api.ts:20-70`, `lib/voice-lifecycle.ts` | memory |
| 11 | Cue/celebration dedupe | `sessionStorage audli-cue:*`, `audli-mascot-v1:*` | `voice-lifecycle.ts:acknowledged`, `mascot-celebration.ts` | tab-scoped |

Eleven owners; only 1–4 are durable. Phases 5–6 together decide what a learner sees **after the lesson is completed**, and that position is not on the server.

### 1.3 Findings: why lifecycle failures keep recurring

1. **Post-completion position is browser-only and tab-scoped.** `/lessons/current` returns only non-completed lessons (`repository.py:465`, `status != 'completed'`); the partial unique index `one_open_lesson_per_learner` also excludes completed. Restore therefore depends on `sessionStorage[audli-session:scope]` (`page.tsx:135-141`). New tab, iOS PWA relaunch, storage clearing, or another device → the learner lands on Home and never sees feedback/homework/stats for the lesson they just finished. This is the single largest correctness gap and is cheap to fix server-side.
2. **Imperative async orchestration in one component.** `structuredLesson` / `exerciseConversation` (`page.tsx:172-303`) are `while (operation.current)` loops that interleave audio playback, recording, uploads and server transitions, with 13 refs mirroring state for "async continuations that must not read stale renders" (`page.tsx:88`). Every screen change inside the loop (`setReviewLesson`, `setCompletion`, `send(...)`) is a side effect of an awaited promise, not of a state transition. This is why each fix (AUD-63, 65, 67, 68) added another ref/guard.
3. **Audio events drive server transitions.** The server trusts the client to report that speech finished: `/lessons/{id}/heard`, `/introduction-heard`, `/conversation/listened`, `/conversation/ready` (`lesson_api.py:202,211`; `conversation_api.py:123,193`). A delayed/duplicated `ended` event is a delayed/duplicated transition request. The server's revision CAS makes this *safe* (409 on stale) but the client then has to interpret 409s (`lessonAction`, `page.tsx:228-237`).
4. **One shared voice lifetime, five owners.** `useVoiceLifecycle()` is instantiated in `Product`, `SpokenOnboarding`, `ListeningHomework` (local fallback), and `AdaptivePlan`; `HomeworkReview`, `LessonTopicCheckin` and `AssignmentCard` borrow `Product`'s instance. AUD-68 root cause 3 documents the failure class: a child's `voice.stop()` closed the lifetime that also owned the parent's operation. `cancelVoice()` was patched to cancel only its own operation, but the hazard (child can reach `stop()`) is still in the shared API surface.
5. **Optional features entangled with the happy path.** Accent discovery/consent logic is interleaved with the WELCOME/REVIEW transition in `lesson_api.answer` (≈170 lines, `lesson_api.py:251-424`, four near-duplicate branches setting `WELCOME_ACK`/`REVIEW_ACK`). The server phase set grows whenever a feature needs a conversational beat (`accent_topic_review_pending`, `accent_confirmation`, `topic_review_due`, `introduction_pending`).
6. **Homework resolution is a many-input predicate.** `ListeningHomework`/`AssignmentCard` (268 lines, 8 refs) calls `onResolved` from five paths (decision saved, polling finished, restored decided, homework off, skip). AUD-68 documents the table of what resolves it; it is correct but it is logic that belongs in a pure function plus one server field.
7. **Global server lock (adjacent, not lifecycle).** `main.py:46` creates one `asyncio.Lock()` shared by all routes and all learners; the container runs `--workers 1` (`Dockerfile`). `lesson.upload` (STT), `turn.assessment` (LLM) and `lesson.answer` hold it across provider calls. AUD-21 docs say "per-learner locks"; the code I read has no per-learner lock. If accurate, concurrent learners queue behind each other's STT/LLM calls. **Verify with a two-user test before relying on this; it matters more for latency at scale than any transport change.**
8. **Page count is not the cause.** AUD-68's reproduced bug (preferences after completion) was a *placement/ownership* error, not navigation. Fewer visual pages does not change findings 1–6.

### 1.4 Is it over-engineered?

Not in the sense of "too many abstractions" — `LessonLifetime/LessonOperation`, CAS checkpoints, the pure reducer and 15 kLoC of tests are appropriate for a voice product with strict rules. It is **under-centralised**: the right ideas (forward-only reducer, CAS revisions) exist but are applied to only part of the flow, and the rest is held by refs and storage hints. The remedy is consolidation of authority, not a rewrite.

## 2. Proposed four-page architecture

Keep **one Next route (`/`)**. Do not add routes: URL changes would add history/back-button, remount and auth-callback surface, which are exactly the failure class being removed. The "pages" are presentation stages of one session controller.

```
          ┌────────────────────────── SessionController (single authority) ───────────────────────────┐
          │ sessionId · stage · revision · homeworkDecision · prefCheckin · recovery                   │
          └──────────────┬──────────────────┬──────────────────┬───────────────────┬─────────────────┘
                         │ renders          │                  │                   │
   PAGE 1 WELCOME&SETUP  │   PAGE 2 LIVE LESSON      PAGE 3 FEEDBACK&HOMEWORK   PAGE 4 CELEBRATE&RESULTS
   HOME? TOPIC_SETUP?    │   LESSON (exercise loop)   FEEDBACK → HOMEWORK        SUCCESS → STATS → (Return Home)
   SMALL_TALK            │                            (explicit resolve/skip)     (explicit exit only)
   HOMEWORK_PREFLIGHT    │
          ▲ voice/mascot layer (one persistent MascotPresence + one VoiceSession, driven by voice events only) ▲
```

Mapping from today's phases:

| New page | Existing phases | Existing components (reuse) |
|---|---|---|
| 1 Welcome & Setup | `HOME` start action, `TOPIC_SETUP`, `SMALL_TALK`, `HOMEWORK_PREFLIGHT` | `LessonTopicCheckin`, `HomeworkReview`, server `WELCOME…TRANSITION` |
| 2 Live Lesson | `LESSON` | `exerciseConversation`, `useVoiceLifecycle`, `SessionShell` |
| 3 Feedback & Homework | `FEEDBACK`, `HOMEWORK` | server `CLOSING`; `ListeningHomework` |
| 4 Celebration & Results | `SUCCESS`, `STATS` | `LessonCelebration`, `RiveMascot`, completed `SessionShell` |

Honest caveat on the brief: Page 3 as specified ("summarize performance… discuss strengths… persist the decision") is **not** what the product does today. The summary (`completion-summary`: exercises completed, strength, next focus) only renders at `STATS`. Moving it to Page 3 is a UX change, not consolidation; the data is already in the `COMPLETED` checkpoint (`strength`, `improvement_focus`, `exercises_completed`).

## 3. Session-controller design

### 3.1 Principle

One **pure** reducer (extend `lesson-phase.ts`, do not replace it) over **one durable record**. The UI renders `controller.stage`; audio, AI and timers emit *requests* carrying `(sessionId, stageEpoch, idempotencyKey)`; only the controller (server-validated) changes stage.

### 3.2 Backend-authoritative record (recommended)

Add to the existing `Lesson` JSON (`lesson_lifecycles.data`, CAS on `revision`):

```
wrapup_stage:  'NONE' | 'HOMEWORK' | 'SUCCESS' | 'STATS' | 'CLOSED'   # monotonic after COMPLETED
homework_decision: 'pending' | 'assigned' | 'declined' | 'unavailable' | 'skipped' | 'not_applicable'
```

* Reuses the existing CAS (`save_lesson(expected_revision)`): a duplicate/stale request gets 409, a duplicate of an *already applied* transition returns the current record (idempotent).
* New endpoint, e.g. `POST /api/lessons/{id}/stage {to, revision}` and `GET /api/lessons/latest-unclosed` (completed but `wrapup_stage != 'CLOSED'`). The server enforces the transition table in §4 (e.g. `SUCCESS` only from `HOMEWORK` with decision ≠ `pending`; `CLOSED` only from `STATS`).
* **No DDL needed** if added as optional fields with defaults inside the JSON `data`. But see rollback risk §9: Pydantic `StrictModel` rows written by new code must still validate in the previous release — the repo already hit this (AUD-40 "preserve snapshots on rollback", `lesson.py:34-37`). Ship the model fields in a **bridge release first**, write them in a second release.
* The homework assignment remains the source of truth for its status; `homework_decision` is a denormalised summary written in the same transaction as the decision, so Page 3's gate is a single read.

### 3.3 Frontend

* `useSessionController()` = reducer + effect that reconciles with server record on: mount, `pageshow`, `visibilitychange→visible`, `online`, and every mutation response. `PhaseState` gains `epoch` (server `revision`); any event with `revision < state.revision` is dropped (stale-event protection).
* Events from audio/AI are wrapped: `controller.request('heard', {lessonId, revision})` → returns the authoritative record; the UI never calls `send` directly from a promise continuation.
* Delete (after parity): `completion` state, `homeworkVoice`, `farewellDue`, `offeredTopic`, `topicDone`, `reviewDone` refs, and both `sessionStorage` post-completion keys (`audli-session:*`, `*:homework`). Keep `audli-mascot-v1:*` (presentation de-dupe only).
* Terminal states: `CLOSED` (server) and `HOME`. `STATS` accepts only `return_home`.

### 3.4 Recovery matrix

| Scenario | Today | With controller |
|---|---|---|
| Page refresh mid-lesson | `/lessons/current` → resume (works) | same |
| Refresh at HOMEWORK/SUCCESS/STATS | works only if same tab's `sessionStorage` intact | server returns unclosed completed lesson + `wrapup_stage`; celebration never replays (`SUCCESS` restores to `STATS`, as today) |
| iPhone Safari backgrounded / tab discarded | recorder interrupt handled (`voice-lifecycle.ts` `visibilitychange`/`pagehide`); post-completion position lost if tab discarded | reconcile on `pageshow`/`visibilitychange`; position is server-side |
| Network disconnection | per-request errors; `Retry conversation` reloads checkpoint | same, plus controller shows `recovering` and replays only idempotent reads |
| Duplicate API response | CAS 409 / `lessonPhase` returns same object | same; duplicate stage request returns current record |
| Delayed audio callback | `operation.current` guards | additionally dropped by `revision` mismatch |
| Return to unfinished lesson | `/lessons/start` returns existing | same |

## 4. State-transition table

Forward-only within a session. "Guard" is enforced on the server for server-owned stages and in the pure reducer for browser-only ones.

| From | Event (requester) | To | Guard | Idempotent duplicate |
|---|---|---|---|---|
| HOME | `start` (user) | TOPIC_SETUP *or* SMALL_TALK | mic ready; planner check-in due & milestone not deferred | returns existing lesson |
| TOPIC_SETUP | `topic_resolved` (user/answer/skip) | HOME → SMALL_TALK | none; only reachable from HOME with no lesson | no-op |
| SMALL_TALK | server `WELCOME…REVIEW_ACK` → `heard` (audio) | HOMEWORK_PREFLIGHT | revision match | 409 → reload |
| HOMEWORK_PREFLIGHT | review done/skip, or homework disabled | LESSON | `TRANSITION` complete | no-op |
| LESSON | `advance` after `READY_FOR_NEXT` | LESSON (next exercise) / FEEDBACK when `closing_due` | assessment persisted | 409 → reload |
| FEEDBACK | `heard` (closing speech; failure tolerated) | HOMEWORK | server `COMPLETED` written | returns COMPLETED |
| HOMEWORK | `homework_resolved` (decision persisted / skipped / n/a) | SUCCESS | **decision ≠ pending** (server-checked) | no-op |
| SUCCESS | `celebrated` (Rive complete / 15 s timer / reduced motion) | STATS | stage == SUCCESS | no-op |
| STATS | `return_home` (user only) | CLOSED → HOME | stage == STATS | no-op |
| any | `end` (user) | paused (server) → HOME | lesson not completed | no-op |
| any | event with stale `lessonId`/`revision` | — | ignored | — |
| forbidden | topic preferences after LESSON begins; SUCCESS before decision; STATS→anything but HOME; backwards moves | — | rejected | — |

The browser-only reducer and its exhaustive test (`tests/lesson-phase.test.mjs`) already encode most rows; the change is that `HOMEWORK→SUCCESS→STATS→CLOSED` becomes durable.

## 5. Component and route consolidation plan

| Path | Action | Notes |
|---|---|---|
| `web/lib/lesson-phase.ts` | **Extend** | add `revision`/epoch, `CLOSED`, server-sourced events; keep pure |
| `web/app/page.tsx` (`Product`) | **Split** | extract `useSessionController`, `LessonStage` (loop), page components; target < 150 lines orchestration |
| `web/lib/voice-lifecycle.ts` | **Modify** | expose a *scoped* handle (`voice.scope()` → own operation) and remove `stop()` from child-visible API |
| `web/components/listening-homework.tsx` | **Modify** | compute `resolved` from server `homework_decision` rather than 5 callbacks; keep voice flow |
| `web/components/lesson-topic-checkin.tsx`, `homework-review.tsx` | **Reuse** | become Page 1 sub-steps; each emits one `done` |
| `web/components/lesson-celebration.tsx`, `rive-mascot*.tsx`, `lib/mascot-celebration.ts` | **Reuse unchanged** | Page 4 |
| `web/components/product-shell.tsx` (`SessionShell`) | **Modify** | one persistent shell for pages 1–4 |
| `web/components/audli-mascot.tsx` | **Reuse** | persistent voice-state mascot (see §8) |
| `app/lesson_api.py` `answer` | **Refactor (later)** | extract accent branch to its own module; keep behaviour |
| `app/lesson.py`, `storage/repository.py` | **Modify** | `wrapup_stage`, `homework_decision` (additive) |
| `app/planner_api.py` `/lessons/{id}/topic-checkin` | **Retire later** | already inert for new clients (AUD-68 doc) |
| `web/lib/conversation-intent.ts`, `sessionStorage` completion keys | **Retire after parity** | replaced by server record |
| Routes | **No change** | single `/` |

## 6. Voice infrastructure comparison

### 6.1 Today (measured, with limits)

Pipeline per exercise turn: silence detect (**fixed 1.8 s**, `hands-free.ts`) → MediaRecorder blob → upload via Next rewrite (`next.config.ts`) → FastAPI → `gpt-4o-mini-transcribe` (`config.py:31`, `language='en'`, `logprobs`→confidence) → separate `/assess` call, `gpt-4.1-mini` structured evidence (`config.py:30`) → deterministic follow-up/adaptation → ElevenLabs `eleven_flash_v2_5` tutor TTS (voice `yKYzqEa22xh5PdidhN70`, OpenAI `coral` fallback, `tutor_tts.py`) → ffmpeg sanitise → full-body download → play.

Evidence in repo (`docs/AUD-21-*.md`, working tree):

| Item | Value | Confidence |
|---|---|---|
| Browser total, last-voice → `playing` (4 dev-mode turns) | 9.8, 10.3, 12.1, **43.2 s** | small sample, not p50/p90 |
| Transcription request | 1.3–2.6 s (33.8 s outlier; ~16 s unattributed post-STT gap) | dev-mode; cause unproven |
| Assessment request | 3.4–5.5 s (provider parse ≈4.2 s) | dev-mode |
| Coach audio request | 2.0–3.1 s (older OpenAI TTS) | dev-mode |
| ElevenLabs production TTS (n=11) | first byte median 139 ms; complete 264 ms; validated readiness median 797 ms (up to 1.6 s) | production logs, tiny n |
| Compact-evidence experiment | −177 ms median, worse quality → rejected | 600 rows |

Observation that matters for the Realtime decision: **assessment (3.4–5.5 s) is the largest component and is application-owned by design** (evidence-grounded, validated, then adapted; AGENTS.md "Application code owns state, scores, adaptation"). No transport can remove it. The addressable portion is silence (1.8 s, deliberately preserved), upload+STT (1.3–2.6 s), and TTS readiness (≈0.8–3 s) — roughly 4–6 s of ~10–12 s.

### 6.2 Realtime API — documented vs hypothesis

Documented (developers.openai.com, fetched 2026-10-18):
* Browser connects over **WebRTC**; server mints a short-lived credential via `POST /v1/realtime/client_secrets`; browser posts SDP to `/v1/realtime/calls`. Client-secret TTL configurable 10–7200 s (default 600).
* Barge-in/interruption is listed as a capability; mechanics not detailed on the pages I read.
* **Transcription-only sessions** (`type: "transcription"`) exist; recommended model `gpt-live-transcribe` streams deltas. It **does not return confidence scores, word timestamps or speaker labels**, and does not support `server_vad`/`semantic_vad` (use client VAD + `input_audio_buffer.commit`). `gpt-transcribe` returns detected `languages`, WebSocket only.
* Flagship audio pricing page: audio in $32 / cached $0.40 / out $64 per 1M tokens; text in $4 / out $24 (page labels the model "GPT-Realtime-2"); realtime transcription listed at $0.017/min. A mini-model price from a third-party article is **unverified**.
* Docs do not state max session length (a community post says 30 min — **unverified**), reconnection semantics, Safari guidance, or custom-voice support.

Hypotheses requiring measurement: lower end-to-end latency; Vietnamese-accented English accuracy; iOS Safari WebRTC stability across lock/app-switch; real per-lesson cost.

### 6.3 Direct answers to the ten questions

1. **Current latency & sources** — §6.1. No representative production p50/p90 exists (the AUD-21 doc says so itself); first measurement step is production-safe numeric timing, not a transport change.
2. **WebRTC vs Render transport** — Learner audio currently goes browser → Next rewrite → FastAPI → OpenAI. WebRTC to OpenAI would bypass Render for that leg, but (a) AGENTS.md requires the *server* to validate audio size/type/signature/decodability/duration and delete it after transcription — direct-to-OpenAI audio is never seen by the server, so that rule would need an owner-approved exception; (b) tutor TTS (ElevenLabs, sanitised by ffmpeg, cached) would still traverse Render. Net transport win is small and policy-costly.
3. **Streaming responsiveness** — Plausible for STT (partials) and for TTS first-byte; not for assessment. Streaming TTS needs a design for sanitisation/ownership/cache (AUD-21-TTS-FEASIBILITY says so).
4. **Vietnamese ESL recognition** — **Unknown.** No documentation claims; I found no Vietnamese-accent fixture set in the tests I reviewed. And `gpt-live-transcribe` returns **no confidence**, while `reliable_recognition` (`app/recognition.py`) treats `confidence is None` as unreliable and `openai_provider.transcribe` derives confidence from logprobs. Adopting live transcription *as the evidence source* would mark every attempt uncertain or force a weaker gate — violating "Preserve STT uncertainty." A safe use would be partials for UI only, with final evidence still from the existing batch transcription of the same audio (which keeps today's latency for that step).
5. **Interruption/barge-in** — Native in speech-to-speech; for Audli's current design (serial playback, 1.8 s silence, replay) barge-in is a new product behaviour that interacts with the evidence rules (a barge-in during a listening clip must not count as an answer).
6. **Preserve tutor voice** — **Not with option C.** The identity voice is ElevenLabs; Realtime speaks OpenAI voices only (docs show `marin`; no custom voice documented). Passage/accent voices are also ElevenLabs and must stay (accent release evidence under `data/voice-evaluation`).
7. **AI without navigation control** — Feasible: give the Realtime session **no navigation tools**; its output is audio/text only, and the browser translates *recognised intents* into `controller.request(...)` calls the server validates. Do not give the model tools that mutate lesson state.
8. **Browser/iOS Safari** — Docs have no Safari guidance. Audli already handles `visibilitychange/pagehide` for MediaRecorder; WebRTC adds ICE/peer-connection teardown on backgrounding that must be tested on a physical iPhone (AUD-68 already lists iPhone checks as outstanding).
9. **Reconnection/expiry** — Not documented; assume a dropped peer connection loses session context. Audli's durable checkpoints make recovery possible, but a speech-to-speech conversation would need its own re-priming from checkpoint.
10. **Security/cost/observability** — Ephemeral secrets are the documented pattern; keep the permanent key server-side (consistent with "No client secrets"). **Prompt-leak risk:** a client-held realtime session that is told the exercise script/expected units can be coaxed into revealing them, which breaks "Never expose scripts or expected answers before a successfully evaluated spoken attempt." Realtime must never receive gated content. Cost estimate in §6.5.

### 6.4 Options A / B / C

| Criterion | A. Keep pipeline, simplify navigation | B. Keep TTS, add realtime STT/streaming input | C. Realtime speech-to-speech |
|---|---|---|---|
| Complexity | Low–medium (controller + refactor) | High (new transport, dual transcription for evidence) | Very high (second conversation engine, state sync) |
| Latency | Unchanged baseline; fixable low-hanging items (see §7 Phase 0) | Possible −1.5–3 s on STT leg; assessment unchanged | Possible big win for *social* turns; none for gated assessment |
| Cost | Unchanged | + realtime transcription minutes (≈$0.017/min listed) on top of existing batch STT if both kept | Highest per-lesson audio token cost (§6.5) |
| Reliability | Improves most (removes the actual failure sources) | Adds WebRTC lifecycle risk (iOS) | Adds WebRTC + session-expiry + context re-priming |
| Educational quality | Preserved | Preserved if confidence gate kept | Risk: model drifts from deterministic pedagogy; leak risk |
| Voice identity | Preserved (ElevenLabs) | Preserved | **Lost** (OpenAI voices) unless owner approves |
| Policy fit (AGENTS.md) | Full | Needs audio-handling exception for direct WebRTC, or WebSocket via backend (keeps validation, adds hop) | Needs exceptions on audio validation, script gating, voice |

### 6.5 Cost per 10-minute lesson (estimate — measure before deciding)

Assumptions: ~5 min tutor speech, ~3 min learner speech; documented token rates above; audio-token density per minute is **not** in the docs I read, so I use the commonly cited order of magnitude (hundreds of tokens/min in, ~1k/min out) and flag it as unverified. On those assumptions, flagship speech-to-speech lands in the **tens of US cents up to ~$1 per lesson** because conversation context is re-billed each turn (cached rate softens this); the existing pipeline's marginal cost per lesson is a few cents for STT/LLM plus ElevenLabs characters (production logs: 874 chars ≈ 192 provider cost units over 11 syntheses; units ≠ dollars). Treat C as roughly an order of magnitude more expensive per lesson than today until a metered pilot says otherwise.

### 6.6 Recommendation

**A now. Defer B and C behind measurement. Do not adopt C.** Reasons grounded in the repo: the failures are state-ownership failures (§1.3), not transport failures; the largest latency component is application-owned assessment; live transcription lacks the confidence signal the safety rule depends on; and C would drop the ElevenLabs voice identity while risking script leakage. If latency remains the top complaint after A and Phase 0 measurement, run B as a flagged **shadow experiment** (partials for UI only, batch STT still authoritative) on a Vietnamese-accent test set.

## 7. Rive mascot integration

* The shipped asset (`web/public/audli/audli-mascot.riv`, per `artifacts/rive/AUD-64/README.md`) has exactly two animations — **`idle` (looping) and `celebrate` (one-shot)** — driven by ViewModel trigger `AudliMascotControls.celebrate`. It has **no listening/thinking/speaking states**.
* The in-lesson mascot is the SVG/CSS `AudliMascot` (`states: Idle|Listening|Thinking|Speaking|Success|Retry`), already driven by `voice.state` and `voice.activity` — i.e. already decoupled from navigation.
* Recommended: keep **one persistent `<AudliMascot>` bound to `voice.state`** across pages 1–3; switch to the Rive canvas only on Page 4's `SUCCESS` (existing behaviour, with reduced-motion and failure fallbacks). Persistence across pages is achieved by mounting the mascot in the persistent shell, not by new animation work.
* Driving listening/thinking/speaking from Rive would require **new Rive authoring** (more states + ViewModel enum). That conflicts with "preserve existing mascot assets unless approved"; make it an explicit later decision.
* Keep animation state a pure function of `(voice.state, activity, stage==='SUCCESS')`; never read the controller stage for listening/thinking/speaking.

## 8. Testing strategy

Existing base: `tests/lesson-phase.test.mjs` (exhaustive sequences ≤4), `web/tests/lifecycle.spec.ts` (13 tests against a stateful mock), `tests/test_lesson_lifecycle.py` (724 lines), iPhone WebKit projects in CI. Add:

1. **Transition table as data**: one shared JSON table consumed by the TS reducer test and a Python test of the new `/stage` endpoint — every allowed row passes, every other (from,event) pair is rejected.
2. **Idempotency/stale**: replay each event N times and out of order; assert same record, no extra audit rows; stale `revision` ignored.
3. **Server-side wrap-up persistence**: complete lesson → kill client state (clear `sessionStorage`, new context) → reload lands on the recorded stage; celebration never replays.
4. **Homework decision gate**: SUCCESS refused while decision `pending`; each of assigned/declined/unavailable/skipped/not_applicable permits it; failed/timeout does not.
5. **Preference scheduling**: due only every 3rd completed lesson, once per milestone, never after LESSON begins (extend `test_planner*.py`).
6. **Voice**: cancel mid-playback, mid-recording, mid-upload; `stop()` from a child cannot cancel a sibling (regression for AUD-68 §3); duplicate `ended`.
7. **Lifecycle on device**: `pageshow`/`visibilitychange` reconcile (Playwright can emulate; real iPhone checklist from `docs/AUD-68-LESSON-LIFECYCLE.md` §"Still needs a physical iPhone" stays mandatory).
8. **Two-learner concurrency** for the global lock (finding 7): two sessions, one slow mocked STT/LLM; assert independent progress.
9. **STT gate**: any new transcription path must have a fixture asserting `confidence is None ⇒ not reliable` (and poor-grammar-correct-meaning fixtures, per AGENTS.md).

**Safe production instrumentation** (extends AUD-21's numeric-only policy): emit fixed-enum events only — `{stage_from, stage_to, reason, result(ok|stale|rejected), revision_delta, client_ms_bucket}` plus anonymised learner hash scoped per deployment; never transcripts, audio, prompts, URLs or free text. Count `409`, stale-drop and recovery-reconcile events per day; alert on `SUCCESS` reached with `decision=pending` (must be 0) and on completed-but-unclosed lessons older than 24 h. Reuse `Server-Timing`/`AUDLI_LATENCY_TELEMETRY` opt-in; add an aggregate sink (no per-learner export).

## 9. Migration risks and rollback

| Risk | Mitigation |
|---|---|
| Adding fields to `Lesson` breaks the previous release on rollback (`StrictModel`; precedent AUD-40 bridge) | **Two releases**: (1) tolerate-only bridge that reads/ignores new fields; (2) start writing them. Never write before the bridge is live everywhere. |
| New `/stage` endpoint vs old clients | Additive; old client keeps using `sessionStorage` path until Phase 3 removes it |
| Controller regression strands a learner mid-wrap-up | Feature flag `controller_v2` (per-account, like `homeworkEnabled`); fall back to current flow; completed lessons stay readable |
| Voice-lifetime refactor changes cancellation semantics | Land behind existing tests + new cross-cancellation tests; no behaviour change in same PR as scoping |
| Global lock fix changes serialisation guarantees | Separate change; per-learner lock + the same CAS; two-user test first |
| iPhone behaviour untestable locally | CI WebKit + owner device checklist before each phase's rollout |
| Realtime experiment leaks cost/data | Shadow mode, no gated content, capped minutes, separate key, off by default |

Rollback for every phase = flip the flag off; no data migration to undo (additive JSON fields only).

## 10. Recommended phases and complexity

| Phase | Scope | Approx. engineering effort* | Production impact |
|---|---|---|---|
| 0. Measure | Production numeric timing for each turn stage + stage-event counters; two-user lock test; Vietnamese-accent STT sample set (no code path change) | 2–4 days | none |
| 1. Server-durable wrap-up (smallest safe step) | Bridge release for `wrapup_stage`/`homework_decision`; `/stage` + `latest-unclosed`; reducer extension; client reads server record behind flag, still renders today's screens | 4–6 days (+1 bridge release) | additive, flagged |
| 2. Extract controller & scope voice | `useSessionController`, split `Product`, scoped voice handles, homework resolution from server field | 5–8 days | refactor, flagged |
| 3. Four-page presentation | Persistent shell, Page 3 summary, explicit homework confirm, remove old refs/storage keys | 4–6 days | visible UX change |
| 4. Server hygiene | Per-learner locks; split `lesson_api.answer` accent branch | 4–7 days | internal |
| 5. (Optional, evidence-gated) B shadow experiment | Realtime transcription partials only, flagged, non-authoritative | 5–8 days | none visible |

*Single engineer familiar with the codebase; excludes review/QA and iPhone device cycles.

## 11. Expected benefits and success criteria

| Metric | Target |
|---|---|
| Completed lessons whose final screens are recoverable after tab discard/new tab | 100% (today: tab-scoped only) |
| Lessons reaching `SUCCESS` with `homework_decision = pending` | 0 |
| Topic-preference renders after LESSON start | 0 (already structural; keep as invariant) |
| Duplicate celebration / duplicate stats renders per lesson | ≤ 1 / ≤ 1 |
| `Product` orchestration size | 361 → < 150 lines; refs 13 → < 5 |
| Stale/duplicate events causing visible UI change | 0 (measured via counters) |
| Voice cross-cancellation incidents | 0 (test + counter) |
| Latency | establish p50/p90 (none exists); then set target — do not promise ≤5 s without data |

## 12. Open questions for the owner

1. Approve Phase 0 and Phase 1 only? (No UX change, additive.)
2. Is moving the performance summary to Page 3 an intended product change?
3. Is replacing the identity voice (ElevenLabs) ever acceptable? If not, option C is closed.
4. Is a WebRTC-to-OpenAI audio path acceptable given the "server validates and deletes audio" rule?
5. Should the Rive asset be extended with voice states (new authoring), or keep SVG for those states?
