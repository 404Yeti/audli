# AUD-77 Phase 2 — advisory language coaching in Review

Status: implemented behind `AUDLI_REVIEW_IMPROVEMENT_ENABLED` (**off by default**). Migration 007 is **not applied anywhere**. No production provider call is made until the owner approves enabling it. Corrected-answer **audio is not part of this phase.**

## What the learner gets
For an evaluated passage, Review can show (only when the learner asks): *What Audli heard* (the saved answer, unchanged), *Your answer, improved*, *What changed* (word-level, with struck-through/underlined text plus screen-reader labels, not color alone) and *Why* (at most two grammar/vocabulary notes). If the answer already reads naturally Review says so and invents nothing. A suggestion that cannot be shown safely is never shown: the learner sees that Audli would not risk the meaning. Comprehension feedback remains in its own evidence groups; language coaching never changes it.

## Rules (enforced and tested)
- **After evaluation only; advisory.** Never reads or writes scores, adaptation, evidence, lessons, profile or the AUD-47 allowance (`LessonLimits` is never called). The original transcript is never modified.
- **Reliable input only.** The first saved answer is coached when its recognition was confident and certain, or the learner corrected the transcript themselves, and it has 4 to ~200 words (1,200 characters).
- **The provider receives only the learner's response text** (and, for the verification call, that text plus the candidate). Never the script, expected answers, profile or title. OpenAI calls use `store=false`; errors are logged by type only.
- **Meaning preservation is layered; any layer can reject:**
  1. provider self-report (`meaning_preserved`, `adds_information`);
  2. structure: ≤2 notes, every edited word covered by a grounded note, no net content added or removed, bounded length and volume of change;
  3. deterministic meaning classes: negation, modality, quantities/numbers, comparisons, certainty, causality/logic, named entities (including a sentence's first content word, since names cannot be told from words without a dictionary);
  4. an independent provider comparison of the two texts (run only if 1-3 pass).
  Known limit: a synonym-looking noun swap passes 1-3 by design and is caught only by layer 4. The API requires all four.
- **Rejected, not corrected.** Rejection codes are fixed strings; the rejected text is never stored or returned.

## API and limits
`POST /api/exercises/{exercise_id}/review/improvement` (exists only when the flag is on). Authenticated, owner-scoped, requires an evaluated attempt. Idempotent: repeating it, or reopening a stored result, is free. A report read (`GET /api/review/{kind}/{id}`) includes the stored state, makes no provider call and writes nothing.
- **Allowance:** 10 new attempts per learner per rolling 24 h (`AUDLI_REVIEW_IMPROVEMENT_DAILY_LIMIT`). Counted: pending (within its 90 s lease), ready and rejected. **Not counted:** failed, abandoned, busy-refused and cancelled claims. Retry after a failure waits 30 s; 5 failures/abandonments in an hour pause the learner for the rest of that hour; both bounds recover on their own. 429 bodies use `improvement.code` values `improvement_limit|improvement_cooldown|improvement_unavailable` (distinct from AUD-47's `lesson_*` codes).
- **Concurrency:** a one-row-per-exercise partial unique index plus a short transaction (claim) means concurrent requests make one provider call; others receive `pending` (202) and can re-ask for free. At most `AUDLI_REVIEW_IMPROVEMENT_MAX_CONCURRENT` provider calls run per process (extra claims are released and answered 503).
- **No long-held lock:** the claim and the completion are two short owner-scoped transactions (claim serializes only the learner's claims for milliseconds via their profile row). No open transaction exists during the provider call. The account-wide lease in `LearnerAuthentication` is **skipped for exactly one route**: `POST` + lowercase canonical UUID + `/review/improvement` (`auth.LEASE_EXEMPT_ROUTES`); authentication and ownership still run first, and the existing telemetry exemption is unchanged. Tests cover near-miss paths, other methods, anonymous/forged tokens, and (PostgreSQL) that the lease is free during the provider call while ordinary mutating routes still queue.
- Cancellation (client disconnect) marks the claim abandoned immediately; a crashed worker's claim is treated as abandoned after the lease.

## Data and migration 007 (additive, reversible; **not applied**)
`review_improvements` (see `docs/postgres-007.sql`): backend-only (RLS on, no browser roles). Stores status, improved text, notes, rejection code, model, prompt version, claim time and a **hash** of the source answer. No learner audio and no copy of the answer. It is derived advisory data: dropping it loses cached suggestions only.
- Numbering: **007 is reserved for AUD-77**; AUD-69 takes the next free number (coordinated on AUD-69).
- Rollback: `docs/postgres-007-rollback.sql` (manual only). Valid at any time; run it only while the running release no longer needs schema 7.
- Optional until the flag is on: startup accepts schema ledgers `[1..6]` and `[1..7]`; enabling the flag without 007 fails closed at startup.
- **Release order (needs owner approval at each production step):** (1) deploy this code with the flag off (the *bridge*: accepts schema 6 and 7; a release that predates it rejects schema 7, so do not apply 007 before it is live); (2) back up and rehearse 007 on a restored copy as for 006; (3) apply 007 as the database owner; (4) enable the flag only after the retention/privacy approval below. **Do not run `python -m scripts.migrate_postgres` against production before step 1 is done: it applies every missing migration, including 007.**

## Privacy, retention and deletion (open items before enabling)
- Learner text reaches OpenAI (already true for evaluation) with `store=false`. ElevenLabs is **not** involved in this phase.
- Rows reference `users` and `exercises`; account deletion (AUD-34, not yet merged) must delete `review_improvements` before those parents. No retention job exists; rows are small text and can be pruned.
- Provider-side retention for corrected-answer audio (AUD-36) blocks the later audio phase, not this one.

## Operations
Log lines (no text, no IDs): `review_improvement outcome=<ready|rejected|failed> rejection=<code> learner=<hash>`. Disable by flipping the flag off: the route disappears and reports stop including the field; stored rows remain.
