# Daily Listening Challenge: implementation plan (design only)

Status: **proposal, nothing built, no paid job scheduled, no budget changed.** Builds on the curated library (AUD-112); do not start until the library is live.

## 0. Audit (what exists)
* Home is `web/components/listening/LearnerHome.tsx` (Figma 02: Continue, Recently completed, one recommendation). Its logic is pure and tested in `web/lib/home-core.ts`; the new section slots between Continue and Recently completed.
* A shared lesson is a lesson with **no owner row** (`store._visible`); lessons are immutable versions with content-addressed audio, so reuse is free. Library entries (`listening_library_entries`, migration 014) add review state and category.
* Lessons currently require four exercises (`library.REQUIRED_KINDS`). A challenge wants **three comprehension questions** and 60-120 s of audio, so it is a different *shape*, not a different player: reuse `LessonPlayer`, `TranscriptViewer`, `MultipleChoiceExercise`, attempts, scoring and signed audio unchanged.
* Generation runs only in `python -m app.listening.generator` under the per-learner (2/day) quota, a USD ledger, monthly credits and the $2/day assessment guard. Challenge generation must be an **operator/system request** that touches none of those learner counters.
* No timezone concept exists for 2.0 (the 1.x day-limit work is AUD-47/AUD-16 and is not reused).

## 1. Decisions to approve
1. **Reset and timezone.** One challenge per CEFR band per **UTC calendar day**; the learner-facing day is the learner's IANA timezone (stored on the profile, default from the browser, validated server-side). Rationale: generation is global and prepared ahead; a learner's "today" is the challenge whose `challenge_date` equals their local date, so a learner in Tokyo and one in Lisbon each see a challenge for their own date. Challenges for D-1, D and D+1 exist at all times (D+1 prepared ~36 h ahead). Streaks use the learner's local date.
2. **Shape.** 3 multiple-choice questions, 60-120 s audio, existing accents with an `enabled` pair only, rotation across `topic x format x accent` using a deterministic schedule table so repeats are avoided and reviewable.
3. **Publication gate.** Same bar as library lessons (validated script, aligned cues, keys, storage check) **plus** a named human approval in phase 1; auto-publish only after the owner reviews a month of results.
4. **Fallback.** If a day has no published challenge, serve the most recent approved one for that band, labelled "From the archive", never counted toward streaks differently: completing it still counts for the day.

## 2. Data model (new migration 015, additive, applied only on opt-in)
* `listening_challenges(id, challenge_date date, band text check in ('A1-A2','B1-B2'), lesson_id fk lessons, state draft|approved|published|failed, is_fallback_source bool, topic, format, accent, generated_at, published_at; UNIQUE(challenge_date, band))`. The unique constraint **is** the duplicate-generation/publishing guard.
* `listening_challenge_runs(id, challenge_date, band, status, attempts, last_error, cost_micro_usd, started_at, finished_at; UNIQUE(challenge_date, band))`: idempotent scheduler bookkeeping and audit.
* `listening_challenge_completions(user_id fk users cascade, challenge_date, band, attempt_id fk attempts, completed_at, local_date; PRIMARY KEY(user_id, challenge_date, band))`: one row per learner per day; derived, so the attempt remains the source of truth.
* `listening_learner_streaks(user_id pk, current int, longest int, last_local_date date, timezone text)`: updated in the same transaction as the completion insert.
* Challenge lessons are shared (no owner row) but marked `purpose='challenge'` in the challenge row so they stay out of the library catalog and out of "recommended next".
* Erasure: completions and streaks cascade from users; challenge lessons are shared and never erased (see #149 protection).

## 3. API and UI
* `GET /api/listening/challenge/today` returns title, description, band, accent, duration, local date, `status: new|in_progress|completed`, `is_fallback`, and the streak `{current, longest}`. No keys, rubrics or object keys (same response-model rule as lessons). Learner band is chosen on the page (A1-A2 or B1-B2), defaulting from their level; a toggle shows the other.
* `POST` is not needed: Start Challenge opens `/listening/{lessonId}` through the existing attempt route; completion is detected server-side when the attempt completes.
* Home: a prominent card below Continue, above Recently completed: title, description, CEFR chip, accent, duration, status pill (Not started / In progress / Done today), Start Challenge / Resume / Review, and a streak counter. Continue and Recently completed are untouched. Design follows the Figma 02 tokens; a Figma frame should be produced before the UI PR.
* Pure helpers in `home-core.ts` (`challengeCta`, `streakLabel`) with unit tests.

## 4. Completion and streak logic (pure, unit-tested)
`next_streak(last_local_date, today_local, current)`: same day = unchanged; yesterday = +1; otherwise reset to 1. `longest = max`. Completion inserts are idempotent (primary key). A fallback challenge counts. No streak freezes in phase 1 (gamification is "not approved": keep it to a counter, ask before badges/rewards). Time-zone changes cannot create two completions for one `challenge_date` because the key is the challenge date.

## 5. Generation and scheduling
* A separate scheduled process `python -m app.listening.challenge_scheduler` (a Render cron job, **not created until approved**) runs hourly, inserts a `challenge_runs` row for each missing (date, band) for D+1 and D+2 (`ON CONFLICT DO NOTHING`), and a lease on the run row gives single-writer semantics. It enqueues a normal generate job as a **system principal**, not a learner; the generator is unchanged apart from accepting that principal.
* The topic/format/accent comes from a reviewed rotation table (data, not model choice); the interpret step is skipped.
* Publish: draft -> validation (existing readiness checks, 3 questions, 60-120 s) -> `approved` (human, phase 1) -> `published`. Failure leaves the run `failed`, alerts, and the fallback serves.
* Kill switch honoured, global ceiling reserved before the call (`ai_budget.py`); a dedicated `AUDLI_CHALLENGE_DAILY_CAP_USD` sub-ceiling inside the global one, default 0 (= disabled).

## 6. Cost model (estimates, not billing)
Two lessons/day, each ~90 s of speech (~1,400 characters) plus script/exercise generation. Use the measured per-lesson cost from the pilot (AUD-112) as the unit `C`; the challenge cost is `2C` per day, ~`60C` per month, plus storage (a few MB/day, negligible) and zero marginal cost per learner (shared audio, deterministic scoring, no spoken-answer assessment). Spoken assessment is not part of challenges, so the $2/day assessment guard is untouched. The figure `C` must come from the pilot before the cap is set; retries are bounded at 2 per (date, band).

## 7. Failure handling
Generation fails -> run `failed`, fallback served, alert; two consecutive failed days -> page the owner. Audio validation fails -> never published. Scheduler crash mid-run -> lease expiry, next tick resumes (idempotent). Clock skew -> dates are computed from the database clock. A published challenge found bad -> `unpublish` like a library lesson; the fallback takes over; completions stay.

## 8. Testing
SQLite always plus real PostgreSQL: unique-constraint races for generation and publish, streak boundary tests (midnight, DST, timezone change, gap, same-day replay), learner isolation, answer-key isolation, fallback selection, cost-cap and kill-switch refusal, challenge does not consume the 2/day private quota, library catalog excludes challenge lessons, erasure keeps challenge audio, Playwright for Home states (new, in progress, done, fallback, empty).

## 9. Incremental PRs and Linear issues
1. Schema + migration 015 + runbook (no behaviour). 2. Pure streak/date logic + tests. 3. `GET challenge/today` + completion hook (flagged off). 4. Home card UI + Playwright (flag `AUDLI_DAILY_CHALLENGE_ENABLED`). 5. Rotation table + system-principal generation + validator (manual trigger only). 6. Scheduler process + runbook (**requires approval to enable**). 7. Fallback + alerting. Each ships dark; only 6 spends money, and only after approval of the cost cap.
