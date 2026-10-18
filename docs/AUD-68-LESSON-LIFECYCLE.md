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

 Start ─► SMALL_TALK ─► HOMEWORK_PREFLIGHT ─► LESSON ─► CLOSING ─► COMPLETED
                                                                   │
                                      celebrationLesson (state) ◄──┤
                                                                   ▼
                                        ┌── !wrapupReady && !wrapupFinished ──► TOPIC PREFERENCES
                                        │          (completedTopic / presentedCompletion refs,
                                        │           reset by remount, restore, second COMPLETED)
                                        └── wrapupReady ─► homework + stats ─► Finish ─► Return Home

AFTER (one forward-only model; preferences exist only before a lesson)

 HOME ─► TOPIC_SETUP? ─► HOME ─► SMALL_TALK ─► HOMEWORK_PREFLIGHT ─► LESSON ─► FEEDBACK
                                                                                  │
          server checkpoint (authoritative while a lesson is open)  ──────────────┘
                                                                                  ▼
                 browser-only presentation phases:  SUCCESS ─► WRAPUP ─► FINISHED ─► HOME
                                                   (Rive)     (homework  (explicit    (explicit
                                                               + stats)   Finish)      Return Home)
```

Rules, all enforced by the pure reducer and tested exhaustively (every event sequence up to length 4):

* Phases only move forward within one lesson; duplicate/delayed/stale events return the *same state
  object* (idempotent, no re-render).
* `TOPIC_SETUP` is reachable only from `HOME` with no lesson. From any later phase the event is a no-op.
* After `FEEDBACK` a lesson never reaches `TOPIC_SETUP`; only an explicit `home` event leaves `FINISHED`.
* A checkpoint for a different lesson is accepted only from `HOME`/setup; otherwise it is stale.
* A restored completed lesson resumes at `WRAPUP` or `FINISHED` and never replays the celebration.
* The screen is stamped `data-lifecycle-phase` on `<html>` so tests (and support) can read the phase.

Persisted vs client-only: lesson phases up to `COMPLETED` are server checkpoints; `SUCCESS/WRAPUP/FINISHED`
are presentation (restored from `sessionStorage` exactly as before). No migration, no contract change.

## Behaviour changes to be aware of

* The three-lesson check-in moves from "after the lesson" to "when you press Start, before the lesson".
  The server contract is unchanged; the client now uses `GET /plan` + the existing answer/skip
  endpoints, which the server already only accepts while *no* lesson is open.
* Because the claim is no longer used, an unanswered check-in is offered again at the next Start
  until answered or *Keep my topics*. This is the "unanswered preference restored after interruption"
  category. If nagging is a concern, cap it with a counter on the server in a follow-up.
* Order of the success page vs homework is **unchanged** from PR #49 (celebration → wrap-up with
  homework and stats), which differs from the numbered flow in the request (homework → success →
  stats). Moving homework ahead of the celebration is a larger UX change; I left it for owner decision.
* `POST /lessons/{id}/topic-checkin` and `Roadmap.offered_checkins` remain for older clients/rollback.

## Tests

* `tests/lesson-phase.test.mjs` — reducer invariants (exhaustive over sequences), in `npm test`.
* `web/tests/lifecycle.spec.ts` — 12 browser tests against a stateful mock of the lesson/planner
  contracts: full lifecycle order; not-due; repeated submission; duplicate completion / late audio /
  `visibilitychange` / `pageshow(persisted)`; delayed farewell; remount + restoration + final-stats
  persistence; interrupted-in-setup restore; homework accepted/declined/unavailable; history
  traversal; explicit Return Home and a clean next lesson. Added to the iPhone WebKit CI configs.
* Updated `completion-regressions`, `lesson-wrapup`, `classroom-polish` specs that encoded the old placement.
* Mock tests establish plumbing and ordering, not provider behaviour.

## Still needs a physical iPhone

1. Safari: complete a lesson on the third completion; confirm the topic question is spoken/typed
   **before** the greeting, never after feedback, and the success page then the final card follow.
2. Background the app (lock, app switch) during setup check-in, small talk, closing and wrap-up;
   return; confirm no screen repeats and nothing is skipped.
3. Press *Finish lesson* while the homework prompt is listening (the stall fixed here).
4. Audio-unlock: the setup check-in speaks after the Start tap and the microphone permission prompt;
   confirm it is audible on first run.
5. Capture the screen if anything topic-like appears after feedback: it would be the homework offer.
