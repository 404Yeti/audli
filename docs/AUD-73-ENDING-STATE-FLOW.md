# AUD-73 — lesson ending: intended state flow and ownership invariant

Written **before** the implementation (AUD-73 steps 0–4). Background and evidence: `AUD-73-ENDING-LIFECYCLE-INVESTIGATION.md`.
Scope: web only. Server lesson checkpoints, adaptation, the evaluator, ElevenLabs/Coral voice, homework persistence and the Rive asset are unchanged.

## 1. Learner-facing flow
```
HOME ─▶ SETUP ─▶ LESSON ─▶ WRAP-UP ─▶ SUCCESS ─▶ HOME
```
| Learner-facing | Reducer phases (`web/lib/lesson-phase.ts`) | Screen |
|---|---|---|
| HOME | `HOME` | Home |
| SETUP | `TOPIC_SETUP` → `SMALL_TALK` → `HOMEWORK_PREFLIGHT` | topic check-in (when due), greeting, previous-homework review |
| LESSON | `LESSON` | live listening |
| WRAP-UP | `FEEDBACK` → `HOMEWORK` | spoken class feedback, then the new-homework decision |
| SUCCESS | `SUCCESS` (terminal) | **one screen**: Pip's celebration, the final statistics, homework status, **Return Home** |
The former `STATS` phase and the `celebrated` event are removed. Success no longer waits for, or is advanced by, the animation.

## 2. Transitions (forward-only; every other event is a no-op that returns the same state object)
| From | Event | To | Sole owner of the event |
|---|---|---|---|
| `HOME` | `topic_setup` / `topic_resolved` | `TOPIC_SETUP` / `HOME` | `Product.topicSetup` |
| `HOME`/setup | server `checkpoint` (WELCOME…TRANSITION, EXERCISE) | `SMALL_TALK` / `HOMEWORK_PREFLIGHT` / `LESSON` | `Product.structuredLesson` (server checkpoints) |
| `LESSON` | server `checkpoint` CLOSING | `FEEDBACK` | `Product.structuredLesson` |
| `FEEDBACK` | server `checkpoint` COMPLETED (closing speech `heard`) | `HOMEWORK` | `Product.structuredLesson` |
| `HOMEWORK` | `homework_resolved(lessonId)` | `SUCCESS` | **`Product.settleWrapup`** (called only by the homework step's single `onResolved`) |
| `SUCCESS` | `home` | `HOME` | **`Product.returnHome`** (the Return Home button) |
| `HOME` | `restored(lessonId, homeworkResolved)` | `SUCCESS` if resolved, else `HOMEWORK` | `Product.restore` (once per mount) |

## 3. Ownership invariant
**Each phase transition has exactly one owner, and only that owner may emit its event.**
1. The *reducer* is the only thing that changes `phase`; it ignores stale lesson ids, repeats, and any backward move.
2. Server-driven phases up to COMPLETED are advanced only by `structuredLesson`.
3. `HOMEWORK → SUCCESS` is advanced only by `settleWrapup`. Components inside the homework step (voice decision, typed reply, skip, discovery polling, confirmation audio, "already decided", "feature off", "no assignment", load error) are **signals, not owners**: they funnel into one per-lesson once-guard (`ListeningHomework`) that emits a single `onResolved(assignment)`. A second signal is dropped.
4. `SUCCESS → HOME` is advanced only by the Return Home button. Nothing else (timers, animation callbacks, audio, polling, `visibilitychange`, `pageshow`) can leave SUCCESS.
5. Presentation (Pip, farewell speech, prefetch) never owns a transition: it reads the phase and cannot change it.
6. **Rendering is derived from `phase` plus the loaded checkpoint**, not from parallel flags: the wrap-up screen shows as soon as the lesson is COMPLETED; background refreshes never gate a screen.

## 4. Wrap-up and Success behaviour
* **Never blank.** From COMPLETED the screen always shows a heading and either a status line ("Getting your wrap-up ready…") or the homework step. Only one blocking request remains (`POST /lessons/{id}/homework`); `GET /api/homework` is skipped for the lesson card (the account flag already says homework is on); the profile/exercise `refresh` runs in the background and again on Return Home.
* **Question asked once.** The invitation is spoken at most once per lesson (existing server/`autoVoice` semantics + the once-guard); a decided assignment never re-asks (`startVoice` resolves immediately for any non-`offered` status).
* **Success carries its data.** The resolved assignment is handed from wrap-up to Success, so Success needs no second load; after a reload it loads once and shows a status line meanwhile.
* **Pip.** The Rive celebration plays at most once per lesson per tab (existing claim gate), decorative only. Rive assets are prefetched when the lesson reaches COMPLETED (idempotent; skipped for reduced motion/data saver). **This does not claim to fix the unconfirmed iPhone animation issue.** Reduced motion and asset failures show the static Pip with the same layout and the stats already visible.
* **Success is permanent** until Return Home: no timer (the old 15 s cap and the "Continue to results" button are removed).
* **Farewell** speaks once per lesson (session marker), on entering Success; it never gates anything.

## 5. Reload recovery (state lives in tab `sessionStorage`; AUD-69 remains out of scope)
| Reload during | Result |
|---|---|
| wrap-up, before the decision | wrap-up again; the question is asked **once** more (audibly where the browser allows autoplay; the visible Speak/replay controls always work) |
| wrap-up, after the decision was recorded | Success (decision never repeated) |
| celebration / Success | Success with stats at once; no celebration replay; farewell only if it has not played |
| after Return Home | Home |
