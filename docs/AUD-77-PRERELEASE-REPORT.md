# AUD-77 Phase 2 — pre-release validation report

Status: **nothing here is merged, deployed or applied.** Migration 007 is not applied, `AUDLI_REVIEW_IMPROVEMENT_ENABLED` is off, and no real provider call has been made by any test, script or harness. Evidence dated 2026-10-22; PR [#67](https://github.com/404Yeti/audli/pull/67).

## 1. CI and the `ci/test_safety.py` discrepancy
- First CI run on #67 (head `6a24e55`): all 8 required jobs passed (Backend, PostgreSQL integration, Frontend, Browser, Security, Rollback schema 003, Rollback schema 004, CI required).
- **Discrepancy, root cause:** `ci/sitecustomize.py` activates its network guard only when `AUDLI_CI_OFFLINE=1`, which `.github/workflows/ci.yml` sets for every job. My earlier local runs omitted it, so four `test_external_resolution_and_connections_are_blocked` cases failed (identically on untouched Phase 1 code). I first described this as "my venv does not load the CI sitecustomize"; **that was wrong**: it loads, it just was not switched on. With `AUDLI_CI_OFFLINE=1 PYTHONPATH=ci:.` all 10 safety tests pass.
- No protection was weakened or bypassed. The new Phase 2 tests were re-run **with the guard on** (loopback PostgreSQL only) and pass.

## 2. Evaluation harness (mock only)
`python -m scripts.evaluate_review_improvement mock|plan` (`scripts/evaluate_review_improvement.py`, fixtures `tests/fixtures/review_improvement_cases.json`, SHA-256 in the output). It runs **the exact production decision path** (`eligibility` + `coach`) with a scripted provider. It has no paid-execution mode, loads no credentials and cannot open a socket (tested). 56 synthetic ESL responses: 15 legitimate grammar/vocabulary corrections (including 1 learner-corrected transcript), 5 already-natural, 24 meaning-changing candidates (negation 3, modality 3, quantity 3, comparison 2, certainty 3, causality 3, named entities 3, added fact 1, removed fact 1, noun/vocabulary swap 2), 5 comprehension-mistake cases (3 preserved, 2 "fixed"), 5 unreliable/unsuitable transcripts and 2 provider errors.

### Results (scripted candidates; `docs/AUD-77-EVALUATION-MOCK-RESULTS.json`)
| Measure | Result |
|---|---|
| Legitimate-correction acceptance | **16 / 18 (89%)** |
| Legitimate-correction rejection (false rejections) | **2**: `v04` ("I have twenty years" → "I am twenty years old": adds the word *old*, layer 2) and `u06` (adds the modal *could*, layer 3). Both are deliberate strictness probes. |
| Meaning-changing acceptance (false accepts) | **0 / 26** (24 meaning-changing + 2 comprehension "fixers"); caught by layer 2: 3, layer 3: 19, layer 4: 4 |
| Needing layer 4 | With the independent comparison forced to "same meaning", **4 / 26 are accepted** (`m22` removed fact, `m23`/`m24` noun swaps, `c05` fact fixed through vocabulary). Layers 1-3 cannot see these; layer 4 is load-bearing. |
| Already-natural answers acknowledged unchanged | 5 / 5, no verifier call, no notes |
| Comprehension mistakes | learner's wrong fact preserved and only the language fixed: 3 / 3; candidates that silently fix the fact accepted: **0 / 2** |
| Unreliable/uncertain/too short/too long | 5 / 5 blocked before any provider call (zero cost) |
| Provider errors | 2 / 2 recorded as `failed` (timeout, malformed); nothing stored or shown |
| Passage facts sent to a provider | 0 (asserted per case) |

### Latency
- **Mock mode measures only local validation: p50 0.3 ms, p95 0.7 ms, max 2.8 ms.**
- **Provider latency is unmeasured.** An improvement is two *sequential* calls (coach, then comparison unless the answer is already natural or rejected earlier). The endpoint bounds the pair at 30 s (`AUDLI_REVIEW_IMPROVEMENT_TIMEOUT_SECONDS`). A live run must report real p50/p95 per call and per improvement; I will not quote a figure I have not measured.

### Cost (estimate; real usage must replace it)
- Price: gpt-4.1-mini, standard tier, **$0.40 / $1.60 per 1M input/output tokens**, read from OpenAI's pricing page on 2026-10-22 (also the AUD-21 table). No cached-input discount assumed.
- Tokens: chars/4 heuristic (±25%) on the real prompts, strict schemas and scripted outputs: **~719 input / ~59 output tokens per request**; 51 coach + 20 comparison requests in the run.
- **Mean $0.00038 per attempt (p95 $0.00046), about $0.38 per 1,000 attempts.** Maximum per learner per day at the cap of 10: about **$0.005**.
- Before this PR's hardening an SDK retry could send each request up to 3 times (worst case about $0.0015 per attempt, measured by test: 3 requests per failure). Coaching now sends **exactly one request** (`with_options(max_retries=0)`, tested).

### Honest limits of this evidence
1. **It measures the validator, not the model.** Candidates are scripted; how often a real model produces acceptable candidates, or fools layer 4, is unknown.
2. **The fixtures are now a development set.** I fixed two validator defects after seeing them (`need to`/`needs to` flagged as a modality change; the collocation repair `did a decision` → `made a decision` counted as added content). Acceptance of 89% is therefore optimistic. A live run needs fresh, held-out cases.
3. Layer 4's scripted answers are authored, not model judgments.

### Proposed live evaluation (needs your approval; nothing runs without it)
`plan` output (`docs/AUD-77-EVALUATION-LIVE-PLAN.json`): 51 eligible cases × 3 repeats, at most 306 requests, concurrency 1, **zero SDK retries**, 30 s timeout, an exact model snapshot pinned first, synthetic data only, intent logged durably before each request, never resent after an ambiguous failure, stop on budget/price/model/prompt/fixture-hash change. **Reservation $0.41 against a proposed hard cap of $1.00.** There is no execution mode in the repository; one would be written, tested against mocks, and reviewed before any key is read (the AUD-21 precedent).

## 3. Permanent rejection: behavior and a bounded recovery proposal (not implemented)
**Today:** `rejected` is terminal for an exercise and consumes the learner's allowance; `failed` retries after a 30 s cooldown (5 failures per hour pauses the learner); `abandoned` self-heals after a 90 s lease. So transient failures already recover; **false rejections do not.** A deterministic rejection will repeat on every identical model output, but a model is not deterministic, and a prompt or validator improvement can never help an already-rejected exercise.

**Proposal (implemented in the recovery PR; see `docs/AUD-77-REVIEW-IMPROVEMENT.md`), all bounded:**
1. *Classify codes.* **Structural** (`unexplained_changes`, `note_not_grounded`, `inconsistent_notes`, `too_many_notes`, `length_changed`, `too_many_changes`): formatting faults, not meaning evidence. **Meaning-protective** (negation/modality/quantity/comparison/certainty/causality/entity, `content_added/removed`, `meaning_not_preserved`, `information_added`, `meaning_check_failed`): never auto-retried, because retrying until layer 4 agrees compounds its false-accept probability.
2. *One automatic repair retry, structural codes only,* inside the same claim: same learner text, plus a fixed reminder to cover every edit with a verbatim note. No extra allowance, cost at most one more coach (+ comparison) call, per-claim maximum 2 coach + 1 comparison calls, still with `max_retries=0`.
3. *One learner-initiated "Try again" per exercise per prompt version* after a rejection. It is a new attempt and **does** consume one of the 10 daily attempts. After a second rejection the result is final ("no suggestion"). Schema effect (007 is unapplied, so this is free to change): narrow the live-row unique index to `('pending','ready')` so up to two `rejected` rows can exist; the count of rejected rows per `(exercise, prompt_version)` caps retries.
4. *Version recovery.* Bumping `PROMPT_VERSION` makes older-version rejections retryable again (and not counted toward the per-exercise cap), so a validator/prompt fix actually heals false rejections.
5. *Detection.* The existing log line carries the rejection code. Add a daily count by code and alert above an agreed rejection rate; tune from the live evaluation, not from fixtures.
Worst case per exercise: 2 rejected attempts + bounded failures; per learner: 10 counted attempts per rolling day.

## 4. Account deletion (AUD-34): `review_improvements` is not covered
Audited `origin/feat/AUD-34-account-management` (PR #20, draft, last updated 2026-10-12, **44 commits behind main**; `app/account.py::erase_application_data`).
- **Mechanism:** a hardcoded, child-first list of `DELETE`s in one PostgreSQL transaction, plus Auth rows. It does not derive tables from the schema and has no completeness guard.
- **Impact on 007:** `review_improvements` has foreign keys to `users`, `exercises` and `attempts` with no cascade. Once the table exists and holds a row, `DELETE FROM attempts/exercises/users` fails, the transaction rolls back, and the learner gets "Deletion could not be confirmed" (HTTP 503). **This blocks account deletion for any learner with a saved improvement.** Retention is not the only risk; completeness is.
- **Same gap for tables added since the branch point:** `learner_roadmaps`, `homework_assignments`, `homework_reflections`, `lesson_entitlements` (migrations 004–006) are also absent from the branch's list, so deletion would already fail for learners who have them. AUD-34 must be rebased and re-audited regardless of AUD-77.
- **Required changes (for AUD-34, not done here):** add, child-first, `review_improvements` (before `attempts` and `exercises`); `homework_reflections` before `homework_assignments`; `homework_assignments` before `lesson_lifecycles`; `learner_roadmaps` and `lesson_entitlements` before `users`; leave `listening_content` (shared catalog), the budget counters and `schema_migrations`. **Tolerate absent tables** (a schema at version 3–6 has no 004–007 tables) via an existence check, or deletion fails on databases that have not applied them.
- **Guard added in this PR:** `tests/test_schema_ownership_inventory.py` fails until every table is classified as learner-owned (21 including `users`) or not (5), checks the classification against real foreign-key paths, and records that deletion order matters. When AUD-34 lands its erasure list must equal that set.
- **Backups and copies:** as in AUD-34's own review, deletion does not reach operator backups or Supabase backups, and there is no independent deletion ledger, so a restore could resurrect a deleted learner's improvement rows. Improvements are derived text, small, and can be pruned, but they are personal data.

## 5. Provider data retention (both calls)
Both the **coach** call (learner text in, candidate out) and the **independent comparison** call (original + candidate) use the same OpenAI Responses endpoint with `store=false`. Per OpenAI's data-controls page (fetched 2026-10-22, https://developers.openai.com/api/docs/guides/your-data):
- **Training:** API data is not used to train models by default.
- **Abuse-monitoring logs:** retained **up to 30 days** unless longer retention is legally or safety required. `store=false` **is not zero retention.** This applies to both calls.
- **Application state:** the Responses API retains it 30 days only when `store=true` (or by default where unset); Audli sets `store=false`.
- **Zero Data Retention** is for approved organizations only; whether Audli's organization has it is **unverified**.
- Prompt caching (up to 24 h of GPU-local state) applies only above a minimum prompt size that I believe is 1,024 tokens (from memory, not verified this session); these requests are about 700 tokens.

Implications and what this PR changes: (a) the comparison call adds a second copy of the learner's words plus the model's rewrite to the same 30-day log window; (b) **ElevenLabs is not involved** in Phase 2 (AUD-36 only blocks the later audio phase); (c) coaching now sends **one request per call** (no SDK retry copies); (d) provider error messages could echo input, and the generic wrapper logs them to Render, so **coaching calls now log only the exception type** (tested with an error that echoes the learner's words); (e) the Review UI tells the learner, before the first tap, that the answer's text (not voice) is sent to Audli's AI provider. **Still required before enabling:** disclosure in the privacy copy that the text is retained by the provider for up to 30 days; confirmation of the organization's actual controls; owner approval of this retention.

## 6. Verification of endpoint properties
| Property | Evidence (all in PR #67) |
|---|---|
| Authenticated | anonymous and forged-token POST → 401 before the handler (`test_exactly_one_route_matches_the_exempt_shape_and_authentication_still_comes_first`, cross-account test); the lease exemption does not skip auth |
| Account-isolated | foreign exercise → 404 and no row, no provider call; another learner cannot read the improvement in `/api/review`, cannot complete/forge a claim (`finish` filters on the verified learner and is compare-and-set), claims are per learner (`test_cross_account_isolation_and_independent_allowances`, `test_foreign_exercise_id_cannot_be_claimed…`, `test_another_learner_cannot_complete_read_or_supersede_a_claim`); real PostgreSQL |
| Idempotent | repeats return the stored result with one provider call; six concurrent requests make one call (`test_repeating_the_request_is_idempotent_and_free`, `test_concurrent_requests_make_one_provider_call`); concurrent claims across exercises cannot exceed the allowance (SQLite and PostgreSQL threads) |
| Independent of AUD-47 | `LessonLimits.admit*` replaced by functions that raise, with `lesson_limit_mode=enforce` and a learner in cooldown: improvement still works and the ledger is unchanged; AST test shows `improvement_api` never references locks, limits or lesson/profile writers |
| No long lock | no write transaction during the provider call (SQLite probe) and the PostgreSQL account lease is free while it runs but still held by other mutating routes |
| AUD-73 / AUD-74 | no changes to the lesson lifecycle, completion flow, `passage_audio` or voice routing; full browser suite (completion, ending, wrap-up, iOS success) green; the only shared file touched is `auth.py` (one route-shaped exemption) |

## 7. Remaining release risks and approvals needed
1. **Unproven model behavior** (section 2): live, held-out evaluation needs approval and a reviewed runner.
2. **AUD-34 must cover `review_improvements`** (and the four tables already missing) before 007 reaches production, or deletion fails for learners with improvements.
3. **Retention disclosure and approval** for the 30-day provider window covering both calls.
4. **False-rejection recovery** (section 3) is a proposal; today false rejections are permanent per exercise.
5. Deterministic layers are strict and will reject some legitimate fixes (measured 2 of 18 here).
6. Release order unchanged: bridge deploy (flag off) → backup and rehearsal of 007 → apply 007 → enable the flag; do not run `scripts.migrate_postgres` on production earlier.
7. CI for the commits after `6a24e55` must be re-run green before merge; WebKit/iPhone checks run only in CI.
