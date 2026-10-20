# AUD-73 — iPhone lesson-ending regression: investigation

**Status: investigation only. No production code, PR, deploy or flag change.** Code read: `main` @ `b4be057`. Linear: AUD-73.
Evidence: production access logs of the reported lesson (read-only; IDs deliberately omitted), a Chromium reproduction with the measured latencies
(`docs/AUD-73-evidence/ending-repro.spec.ts.txt`, a scratch Playwright spec run against the repo's mock backend and **not** committed as a test), and the source.
**Limits:** WebKit cannot launch in this environment (missing system packages), so iPhone *rendering* could not be reproduced; client telemetry is OFF, so there is no
device-side timing. Where a conclusion depends on that, it is labelled.

## 1. What actually happened on the device (server log, UTC, offsets from the closing speech finishing)
| Offset | Server event (logged at response completion) | Client meaning |
|---|---|---|
| 0 s | `lessons/{id}/heard` | closing speech ended → lesson COMPLETED |
| +1.4 / +2.3 s | `GET /profile`, `GET /exercises/current` | `refresh()` awaited **before** the completion screen is set |
| +4.3 s | `GET /homework` | homework card load #1 |
| +6.9 s | `POST /lessons/{id}/homework` | assignment arrives, the question can finally render |
| +9.9 s | `homework/{id}/invitation-audio` | invitation spoken |
| +27.7 s | `homework/{id}/decision/attempts` | spoken decision accepted (no `confirmation-audio` ⇒ no video was assigned) |
| +33.5 s | `GET /homework` | homework card load #2 = the final stats card mounted |
| +35.5 s | `lessons/{id}/farewell-audio` | farewell |
| +37.6 s | `POST /lessons/{id}/homework` | assignment re-fetched for the stats card |
| +52.9 s | `GET /plan` (no `/profile`) | Home screen's `PlanningInvitation` ⇒ Return Home |

* **There was no reload at the end** (no `/auth/config`, `/lessons/current`, or `/lessons/{id}` restore calls). The new lifecycle ran as designed.
* Earlier in the same lesson `POST /lessons/start` appears twice mid-exercise (03:08:58 and 03:10:27) **without** a reload signature: these are in-page Retry paths (`start(false)`),
  each right after a TTS clip. They suggest two audio-playback failures that needed a manual "Retry conversation". Not part of the ending, but the same fragility class; flagged for follow-up.
* Only one decision happened (`decision/attempts` once, `invitation-audio` once).

## 2. Findings per symptom
### A. Partially blank "Lesson completed" screen — ROOT CAUSE FOUND (high confidence: code + log + reproduction)
`EnabledListeningHomework` renders `null` until two **sequential** requests finish (`GET /api/homework`, then `POST /lessons/{id}/homework`) and has no loading state
(`web/components/listening-homework.tsx`: `if (!enabled || items.length === 0) return null`). The completion screen (`page.tsx`) is already showing the header and the
`<h1>Lesson completed</h1>`, so the learner sees a heading and nothing else. Measured in the real lesson ≈ **4.5 s** (+2.3 s earlier while `refresh()` blocks the screen switch);
reproduced in Chromium with the same latencies: heading-only screen for **4.6 s**. The same pattern recurs on the stats card (card region empty ≈4 s while it re-fetches).
Contributing: `refresh()` (profile + exercise) is awaited although the ending does not need it; `GET /api/homework` is redundant (`account.homeworkEnabled` already says the feature is on);
each request is slow because the API issues dozens of synchronous DB statements per request (AUD-72 finding: ≈118 per turn) — AUD-72 lengthens the gap but is **not** its cause.

### B. Pip did not backflip — UNDETERMINED for the iPhone; several causes ruled out (medium-low confidence)
Signatures measured in Chromium (success screen duration / backflip):
| Cause | Success screen lasted | Backflip |
|---|---|---|
| Normal (real latencies) | 3.2 s | yes (24 frames) |
| iOS Reduce Motion ON | 0.1 s | no |
| `rive.wasm` request fails | 1.0 s | no |
| `.riv` fails | 1.3 s | no |
| celebration already "claimed" this lesson (sessionStorage) | 0.5 s | no |
| `rive.wasm` very slow (14 s) | 11 s | no (10 s timeout → static) |
| `rive.wasm` slow (6 s) | 8.7 s | yes, late |
The real lesson's success phase was **≈3.8 s ± 0.6** (stats card mounted ≈1.8 s before its `GET /homework` completed at +33.5 s; decision processed at +27.7 s). That matches the *normal full lifecycle*
and excludes every instant-skip or failure signature above. So the animation lifecycle most likely ran to completion; whether Pip was **visible** on the iPhone cannot be determined from the server.
Facts that matter: the 2 MB `rive.wasm` is fetched lazily only when the celebration mounts (served correctly as `application/wasm`); CI asserts the actual animation only in Chromium
(`structured-lesson.spec.ts`), while the WebKit/iPhone project only asserts the "Great job!" heading, so an iOS-only rendering failure would pass CI. **Needed:** a screen recording or Safari Web Inspector
console from the device; a WebKit pixel assertion in CI.

### C. Homework/materials question reappearing after success — NOT REPRODUCED; no evidence in the log (open)
* Nominal path: the question is shown exactly once (HOMEWORK), the final card never contains a question for any outcome (skipped / assigned tested; no "?" or "Would you like").
* The server is idempotent (`offer` is get-or-create; a decided assignment never returns to `offered`).
* The only mechanism that re-asks is **restore after a reload** while still in HOMEWORK: reproduced S1/S2 — the question reappears **silently** (no invitation audio; `autoVoice` is lost) and needs a tap.
  No reload occurred at the end of the reported lesson.
* Other asks exist elsewhere: Home "Review your next lessons" (planner check-in), Plan tab, and the next Start's topic check-in. The log shows none of these producing a spoken or API-visible re-ask.
So the reported re-ask is unexplained. **Questions for the owner:** what exact words or screen (spoken or text)? Before or after tapping Return Home? Is iOS Reduce Motion on? Wi-Fi or cellular? Is a screen recording available?

### Restore side effects found while reproducing (real, independent of the reported lesson)
* Reload **during the celebration** → jumps to the stats card; celebration and **farewell are never played**.
* Reload during the homework step → question re-asked **silently** (no audio, learner must tap Speak).
* Post-completion position lives in tab-scoped `sessionStorage` (the AUD-69 gap): restore is deterministic but depends on two storage keys.

## 3. Who owns each transition (current code)
| Phase | Rendered by | What can advance it |
|---|---|---|
| FEEDBACK (server CLOSING) | `Product.structuredLesson` loop | audio `ended` → `heard` → server COMPLETED |
| HOMEWORK | `page.tsx` completion branch + `ListeningHomework`/`AssignmentCard` | `onResolved` from **9 call sites (8 distinct paths)** in `listening-homework.tsx`: voice/typed/skip decision saved (`finally`); discovery poll finished (assigned, and unavailable/skipped); confirmation audio finished; assignment already decided on mount; homework disabled for the account; account feature off (`!view.enabled`); empty assignment list; the load-error "continue" button |
| SUCCESS | `LessonCelebration` + `RiveMascot` | `onComplete` from **5 sources**: Rive StateChange celebrate→idle; Rive failure +0.6 s; reduced motion (immediate); "already claimed" (immediate); 15 s hard cap; plus a "Continue to results" button |
| STATS | completion branch | only Return Home; entering it also starts the farewell and mounts a second homework card |
| any | `lessonPhase` reducer | the only forward-only authority |
**Competing owners: yes.** Two state systems decide what is on screen: the pure reducer (`lifecycle.phase`) and `active` / `completion` / `voice.state` flags set imperatively by the loop. They disagree
for ≈1.3 s in the reproduction and ≈2.3 s in the real lesson (phase HOMEWORK while the live-lesson shell is still displayed), the shared `<audio>` element exists in the live-lesson and completion screens but not in the celebration screen, so it is unmounted for the celebration and recreated for the stats card, and each ending component keeps its own refs (8 in the homework card alone).
The reducer prevents wrong *ordering*; nothing prevents **blank or skipped screens**, because rendering is not derived from it.

## 4. Proposed simplified flow
```
HOME ─▶ SETUP ─▶ LESSON ─▶ WRAP-UP ─────────────▶ SUCCESS ─▶ HOME
                          (one component)         (one screen)  (Return Home only)
                          feedback speech →       Pip celebration + final stats
                          assignment ready →      + homework status + farewell,
                          ask ONCE → decision     all visible together
```
* **WrapUp** owns feedback speech + homework decision as one small internal machine (`speaking → preparing → asking → deciding → done`) and emits exactly one `done(decision)`; it **never renders empty** (always a status line).
* **Success** is one screen: Pip (Rive, decorative and non-blocking) with the stats card visible immediately beneath and Return Home always available. No separate SUCCESS→STATS hop, no `celebrated` gate, no 15 s cap, no "Continue to results".
* The reducer keeps its forward-only guarantee; rendering is derived from `phase` alone (no parallel `active`/`completion` flags for the ending).

## 5. Scoped implementation plan (for approval; web-only, no backend change)
0. **Evidence first (no product change):** owner answers §2C questions and, if possible, sends a recording/Web Inspector log; add a WebKit pixel/frame assertion for the celebration to CI (test-only PR) to learn whether iOS renders the canvas.
1. **Never blank (S):** stop awaiting `refresh()` in the COMPLETED branch (run it after, or on Return Home); fetch only `POST /lessons/{id}/homework` (drop `GET /api/homework` here); start it the moment `heard` returns; render a visible "Wrapping up…" state instead of `null`. Expected: no thin screen, ≈2.6 s → shown, not blank.
2. **One wrap-up owner (M):** fold the 9 `onResolved` call sites into one decision outcome handled in one place; keep the existing voice/typed/skip/discovery behaviour and the closing-choice parsing unchanged.
3. **Merge SUCCESS+STATS (M):** one screen as above; prefetch `rive.wasm`/`.riv` when COMPLETED arrives so the backflip starts at once; keep reduced-motion/static fallback; farewell once on entry, never gating anything.
4. **Restore fixes (S):** restoring into HOMEWORK speaks the invitation (`autoVoice`); restoring after the decision shows the success screen with stats (no silent skip of the farewell policy).
5. **Tests:** see §6. Estimated total **M (≈2 days)** plus a device check. Preserved: adaptive logic, evaluator, ElevenLabs voice (invitation/farewell/closing TTS unchanged), Rive asset and ViewModel trigger.
**Relevance of held work:** AUD-72 shortens the waits (not required for the fix); AUD-69 (server-durable wrap-up state) would make restore independent of `sessionStorage` (relevant to §2 restore findings, not to the reported sequence). Neither is required for steps 0–4.

## 6. Regression tests for the exact failure sequence
1. **No blank ending:** inject the measured latencies (profile 1.3 s, exercise 0.9 s, homework list 2 s, assignment 2.6 s); sample the DOM every ~100 ms from COMPLETED to the question; assert no moment with only a heading (a status or content is always visible). *Fails today (4.6 s thin).*
2. **Backflip contract (Chromium and WebKit-iPhone):** celebration shows a canvas, ≥N distinct frames over ~2 s, exactly one celebrate trigger, stats visible; Reduce Motion / asset failure ⇒ static Pip with stats visible at once (never a silent skip).
3. **Ask once:** `invitation-audio` count ≤ 1 and the question text absent from success/stats/Home across: nominal, reload at each phase (S1–S4), duplicate `visibilitychange`/`pageshow`, lost decision response, 422 clarification.
4. **Restore parity:** reload during the celebration ⇒ success screen with stats and a defined farewell behaviour; reload during homework ⇒ question re-asked audibly once.
5. **Model-based invariant test** over interleavings of {heard, assignmentLoaded, decisionSaved, riveDone, riveFail, timeout, restore}: monotonic phases, ≤1 invitation, ≤1 celebration, ≤1 farewell, never an empty screen.
6. Keep `lifecycle.spec.ts` (order celebration → stats → Return Home) and the recorder spec; add the real-latency variant to the lifecycle fixture.
