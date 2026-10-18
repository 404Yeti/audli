# AUD-68 — Lesson lifecycle: topic preferences after completion

Baseline: PR #49, `7b9688a`. Linear: AUD-67 (the issue was created under that number; the owner request is named AUD-68).

## Verdict

**Reproduced:** on the unmodified baseline, a completed lesson enters the topic-preference UI. The
server log of a faithful stateful mock shows `legacy-offer` after the final `heard` (lesson already
`COMPLETED`), and the browser counts one entry to `.topic-checkin` after the celebration.
This is not a race; it is the designed order. `LessonTopicCheckin` was rendered *inside the
completion view* (`web/app/page.tsx`, `!wrapupReady && !wrapupFinished`) and fetched its offer from
`POST /api/lessons/{id}/topic-checkin`, which only answers for a lesson with `status == completed`
(`app/planner_api.py: offer_checkin`).

**Not reproduced:** the exact "three full entries" seen on a physical iPhone. PR #49's own
duplicate-completion/remount regressions pass on the baseline in Chromium, and my baseline probes
(full flow, reload mid-check-in) produced one entry per completion. Local WebKit cannot launch here
(missing system libraries; `sudo npx playwright install-deps` is needed), so the iPhone WebKit project
was not run locally; CI runs it. I am therefore **not claiming the recurrence is proven fixed on
device**. What is proven is structural: after this change no code path can render preferences once
the lesson has begun (see the invariant tests), so a recurrence of *that* screen after feedback is
impossible by construction. If a topic-like screen still appears on the phone, it is not the check-in;
the homework offer ("…about something you're interested in? … or a topic") is the only other prompt in
that position and uses similar wording.

## Root cause

1. **Preferences were a post-completion step, and their position was held in client-only state.**
   Placement was decided by four unrelated holders: `wrapupReady` (state), `wrapupFinished` (state),
   `completedTopic` and `presentedCompletion` (refs), plus `sessionStorage`. Each was reset by a
   different path (`structuredLesson` COMPLETED branch, Return Home, new `Product` lifetime), so the
   question "may preferences be shown now?" had no single answer. PR #49 added more of the same
   (per-lesson refs). Any lifetime that lost a ref (remount, restored page, a second `start()`
   returning the completed checkpoint) re-evaluated the guards from scratch.
2. **Claim-before-answer.** The server marks the milestone offered when it is *offered*
   (`access(claim_lesson=…)`), not when it is answered. An interrupted check-in is consumed yet stays
   "due", so it returned via the Home banner / Plan screen, and the wrap-up silently skipped it on
   restore (`restore()` set `wrapupReady = true` with no record that nothing was answered).
   Reproduced: reload mid-check-in → no UI, `resolved: null`, banner still shown.
3. **Cross-cancellation between siblings on one shared voice lifetime** (latent, found while building
   the regression suite; pre-existing). `AssignmentCard.cancelVoice()` called the shared `voice.stop()`,
   which closes the lifetime that also owns `finishWrapup`'s operation. Pressing *Finish lesson*
   while homework voice was active left the screen on "Finishing…" forever, in `WRAPUP`, with no way
   to reach the final card. Fixed by cancelling only the card's own operation. I hit the same class
   of bug in my own new setup code (`typed()` calling `voice.stop()`), caught by the lifecycle suite.

### Every path that can render topic preferences

| Path | Before | After | Classification |
|---|---|---|---|
| `LessonTopicCheckin` in completion view (`page.tsx`) | rendered after `COMPLETED` | **removed** | invalid re-entry |
| `POST /lessons/{id}/topic-checkin` (completed lessons) | called by the client | **not called** (endpoint kept, inert for new clients) | invalid |
| `LessonTopicCheckin` in `TOPIC_SETUP` (new) | – | rendered once before a lesson exists, only if `GET /plan` says it is due | **legitimate setup question** |
| Interrupted setup check-in | silently dropped (client) | re-asked in setup on next Start until answered or kept | **restored unanswered preference** |
| Plan screen "Share feedback" / Home banner | available | unchanged (learner-initiated, no lesson open) | legitimate, outside the lesson |
| Lesson `REVIEW` topic question (`topic_review_due`) | off for new lessons, on for legacy saved ones | unchanged | legitimate, in setup |
| Onboarding interests | only while onboarding is incomplete | unchanged | legitimate |

## Authoritative model (`web/lib/lesson-phase.ts`)

```
BEFORE (booleans + refs, preferences inside the completion view)

 Start ─► SMALL_TALK ─► HOMEWORK_PREVIEW ─► LESSON ─► CLOSING ─► COMPLETED
                                                                   │
                                      celebrationLesson (state) ◄──┤
                                                                   ▼
                                        ┌── !wrapupReady && !wrapupFinished ──► TOPIC PREFERENCES
                                        │          (completedTopic / presentedCompletion refs,
                                        │           reset by remount, restore, second COMPLETED)
                                        └── wrapupReady ─► homework + stats ─► Finish ─► Return Home

AFTER (one forward-only model; the exact requested order)

 HOME ─► TOPIC_SETUP? ─► HOME ─► SMALL_TALK ─► HOMEWORK_PREFLIGHT ─► LESSON ─► FEEDBACK
  ▲        (every 3rd lesson,        (server checkpoints WELCOME…REVIEW_ACK / TRANSITION / EXERCISE / CLOSING)
  │         once per milestone)                                                      │
  │                                                                                  ▼ COMPLETED
  │                           browser-only presentation phases (restored from sessionStorage)
  │                                                                                  │
  │      HOMEWORK ──(decision persisted, or explicit skip)──► SUCCESS ──► STATS ──(Return Home)──┐
  │      new-homework query    accepted / declined /           Rive         final card:          │
  │      alone on screen       unavailable / homework off      backflip     stats + feedback     │
  │                                                            once         + homework status    │
  └──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Rules, all enforced by the pure reducer and tested exhaustively (every event sequence up to length 4):

* Phases only move forward within one lesson; duplicate/delayed/stale events return the *same state
  object* (idempotent, no re-render).
* `TOPIC_SETUP` is reachable only from `HOME` with no lesson. From any later phase the event is a no-op.
* `SUCCESS` (the celebration) is reachable only through `homework_resolved`; `STATS` only through
  `celebrated` or a restore. Neither time nor audio events can skip ahead.
* Only an explicit `home` event leaves `STATS`. A checkpoint for a different lesson is accepted only
  from `HOME`/setup; otherwise it is stale.
* A restored completed lesson resumes at `HOMEWORK` (decision not recorded) or `STATS`, and never
  replays the celebration or the farewell.
* The screen is stamped `data-lifecycle-phase` on `<html>` so tests (and support) can read the phase.

### What resolves the new-homework query

`ListeningHomework` reports `onResolved` only for a definitive outcome; transient errors never advance:

| Outcome | Resolves? |
|---|---|
| Voice/typed decision saved as assigned (after the confirmation audio finishes), skipped, or unavailable | yes |
| Discovery still running, then finishing (polling) | yes, when it finishes |
| Assignment already decided (restore, or earlier decision) | yes |
| Homework off for the account, or no assignment for the lesson | yes (nothing to decide) |
| Network/timeout error, uncertain speech, "Skip optional listening" mid-recording | **no**: card stays with its recovery controls |
| Explicit **Skip homework** (saves a skip) | yes |
| Assignment failed to load | no; explicit **Continue without homework** button |

Persisted vs client-only: lesson phases up to `COMPLETED` and the homework decision are server state;
`HOMEWORK/SUCCESS/STATS` position is presentation (a `sessionStorage` hint, as before). No migration,
no contract change.

### Topic check-in cadence and deferral

Asked in `TOPIC_SETUP` when `GET /plan` reports it available (the planner's existing every-third-lesson
milestone, only while no lesson is open). Answering, or "Keep my topics", completes it on the server
(existing endpoints). It is offered **once per milestone per browser**: the milestone number is recorded
in `localStorage` (plus memory) the moment it is shown, so an interrupted or dismissed check-in is
deferred, not repeated at every Start. It stays available in Plan ("Share feedback") and the next
milestone asks again. No backend or schema change.

## Behaviour changes to be aware of

* The three-lesson check-in moves from after the lesson to the Start of a lesson, before the greeting.
* The new-homework query now comes **before** the celebration (as requested). The old "Finish lesson"
  button is gone: the final stats card is reached by the celebration, and **Return Home** is the only
  exit. The spoken farewell now plays once when the final card appears (not on restore) and never
  gates it.
* The final card also shows the homework status (assigned link, declined, or unavailable).
* `POST /lessons/{id}/topic-checkin` and `Roadmap.offered_checkins` remain for older clients/rollback.
* The audio element is re-created when the screen changes from the homework query to the celebration
  (the celebration has no player); playback of the confirmation always finishes first.

## Tests

* `tests/lesson-phase.test.mjs` — reducer invariants (exhaustive over sequences), in `npm test`.
* `web/tests/lifecycle.spec.ts` — browser tests against a stateful mock of the lesson/planner contracts:
  full requested order; not-due; Keep my topics; repeated submission; deferral after interruption;
  celebration waits for the homework decision (duplicate events, `visibilitychange`, `pageshow`, 90 s);
  failed decision stays until completed/skipped; farewell never gates the final card; remount and
  restore (homework query, then final card, silent); homework off; accepted/declined/unavailable;
  history entries; Return Home and a clean next lesson. Also in the iPhone WebKit CI configs.
* Updated `completion-regressions`, `lesson-wrapup`, `lesson-lifecycle-fixes`, `homework-readiness`,
  `classroom-polish` for the new order (Finish lesson removed; decision precedes celebration).
* Mock tests establish plumbing and ordering, not provider behaviour.
* `classroom-polish` timing flakes (5 s polls) appeared only with parallel local workers; CI runs one worker.

## Still needs a physical iPhone

1. Safari, third completion: topic question before the greeting, once; if you leave mid-question it is
   not asked again at the next Start (Plan still offers it).
2. After class feedback: the new-homework question alone on screen; answer by voice and by typing; the
   Rive backflip starts only afterwards, once, then the stats card with homework status.
3. Background the app (lock, app switch) during each phase; return; nothing repeats, skips or navigates.
4. The stats card stays until Return Home; the farewell speaks once and is audible after the celebration.
5. Audio-unlock: the setup check-in speaks after the Start tap and microphone prompt.
