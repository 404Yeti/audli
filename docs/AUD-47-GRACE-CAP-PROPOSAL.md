# AUD-47 — shared enforcement for the closing-grace action cap (proposal only)

Status: **proposal; no code or schema change is made by this document.** Decision needed before migration 006 is finalized.

## Today
After a lesson's 10-minute deadline only the closing sequence is allowed (closing speech, final-feedback cue, farewell, homework and planner
speech/STT) for 3 more minutes. `LessonLimits.grace_actions` caps provider-backed wrap-up actions at 12 per lesson, but the counter is an
in-memory dict. It is correct for one process and wrong as soon as two workers or instances serve the same learner (the cap multiplies by the
number of processes) or after a restart (the count is lost). The 3-minute window itself is already shared, because it is computed from the lesson's
stored `started_at`; only the **count** is process-local. The exposure is small and bounded (12 × workers actions inside 3 minutes, all
application-controlled strings, cached after first generation), but it is not enforceable in the way the daily ledger is.

## Options
| | Mechanism | Pros | Cons |
|---|---|---|---|
| A (recommended) | `first_speech_failures`-style counter on `lesson_entitlements`: `grace_actions INTEGER NOT NULL DEFAULT 0` | Same row, same transaction model, same RLS; one atomic statement; no new table; resets with each claim | One extra column in 006 (unapplied, so free to add now) |
| B | Count rows in `audio_assets` created after the deadline | No schema change | Counts only cached generations; backup-voice audio is intentionally not cached and STT/LLM actions create no row, so it under-counts |
| C | Keep the in-process counter | No change | Not shared; documented as defense in depth only |

## Recommended design (A)
* Column: `grace_actions INTEGER NOT NULL DEFAULT 0 CHECK (grace_actions BETWEEN 0 AND 12)` on `lesson_entitlements`; reset to 0 by the claim upsert (a new lesson).
* Slot taken by one atomic statement, only for provider-backed wrap-up actions at or after the deadline:
  `UPDATE lesson_entitlements SET grace_actions = grace_actions + 1 WHERE user_id = :u AND last_lesson_id = :lesson AND grace_actions < 12 RETURNING grace_actions`
  Zero rows means the cap is reached (or this is not the ledger's current lesson) and the action is refused with the existing 429 `lesson_time_expired` / `lesson_cooldown`.
* The trigger needs no change: it only constrains `last_started_at` and `next_eligible_at`.
* Cost: a write only for actions after the deadline (rare, inside a 3-minute window), none during normal lessons.
* Failure policy: in enforce mode a database error while taking a slot refuses the action (closing text remains visible; audio can be retried inside the window).
* Observe: take no slot, log only. Off: untouched.
* Tests: two repository instances racing for the last slot (exactly one wins, SQLite and PostgreSQL), reset on a new claim, refusal body, no slot for pre-deadline actions, counter survives a process restart.

## Before enabling enforce globally
Apply 006 with the column, replace the dict with the atomic slot, add the race test; until then the in-memory cap stays defense in depth only.
