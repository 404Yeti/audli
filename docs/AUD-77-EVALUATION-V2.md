# AUD-77 evaluation 2: frozen held-out set, seeded calibration set, statistics and plan

**Status: prepared, NOT executed.** No provider has been contacted for this work and no credential was read. Migration 007 is unapplied and `AUDLI_REVIEW_IMPROVEMENT_ENABLED` is off. A paid run needs a new approval file completed personally by the owner (section 7).

Evaluation 1 (55 cases) exposed failures that were then tuned against (`docs/AUD-77-QUALITY-TUNING.md`, PR #77). Those 55 cases are therefore **regression data only**. This document describes the data that can support a release decision.

## 1. The two datasets

| file | what | size | sha256 |
|---|---|---|---|
| `tests/fixtures/review_improvement_heldout_v2.json` | fresh held-out cases | 104 (100 reach the provider, 4 are blocked before any call) | `91bc9d23e319d3ff7d4dabeb1e56fa1ba8dc693fe3c06d35803efcae07bedffd` |
| `tests/fixtures/review_improvement_calibration.json` | seeded meaning pairs for the comparison step | 39 pairs: 27 deliberately unsafe, 12 same-meaning | `00eb8d51fb309c1d2e410af9e676b5f1a204df20922a7d096c314b16568fde2f` |

**Held-out v2** (all synthetic, disjoint from the development set and from evaluation 1's set; a test enforces both, and the runner refuses to start if they ever overlap):
- 64 *fix* cases (a real error with a reference rewrite and required fixes): tense 6, agreement 6, articles 4, prepositions 4, word form 6, collocation 4, word order 3, countable/plural 3, disfluency 2, **false facts that must be kept** 6 (agreement or tense error plus a wrong fact), and meaning-sensitive fixes: negation 3, modality 3, quantity 3, comparison 3, certainty 2, causality 2, named entity 2, number 2.
- **32 already-natural answers** that must come back unchanged: British spelling and vocabulary 6, American 4, plain fluent 6, optional subject/pronoun 3, informal/contractions 3, fluent false facts 5, natural negation/certainty/comparison/quantity/causality 5.
- 4 *ambiguous-intent* answers (e.g. "I will see him last week") where the safe behavior is to leave the answer alone; reported separately and not part of the unnecessary-edit rate.
- 4 answers blocked before any provider call (too short, too long, two uncertain recognitions).

**Calibration** (the comparison step is measured directly with one request per pair, and each pair is also run through the production decision path so layers 1-3 are included):
- 27 unsafe "corrections", each a plausible grammar fix with a verbatim note that also changes the meaning: negation added/removed, number, name, quantity, modality, certainty, cause, comparison, a *fact "corrected"* (Paris→Berlin, 50→100 degrees), detail added, detail dropped, who-did-what swapped.
- 12 same-meaning corrections in the patterns evaluation 1's comparison step rejected (tense after "so", word form, false statement kept, optional pronoun, `will` repair, article, preposition, countable, word order, regional spelling).
- Offline, layers 1-3 reject 23 of the 27 unsafe pairs. **Four (c19 invented cause, c20 cheap→expensive, c26 a person dropped, c27 who-called-whom swapped) pass layers 1-3 and depend on the comparison step alone.** That is a finding about the design, not a tuned-away result: those rules were deliberately not changed for this set.

### How the labels were made, and what that does and does not give you
- Written in one sitting from the product rules and general English usage, **before any provider output on these cases existed**. Every reference rewrite and invariant is checked by tests: each reference passes its own checks, each fix case's source fails its required fixes, and every unchanged source satisfies its own invariants (so returning an answer unchanged can never look like an unsafe acceptance).
- **They are not independently labelled in the strict sense.** The author is the same agent that tuned the validator and prompts, knows their rules, and labelled alone. To limit leakage: no held-out case was run through the validator or a provider before freezing; the only offline check was the label-consistency tests above. Disclosed edits made while building: one held-out source replaced because it duplicated an evaluation-1 sentence (f02), one invariant corrected because the *unchanged source* failed it (f34 required the word "information" that only the corrected text contains), and one calibration note widened so it covered the whole change (c09). The calibration pairs were run through layers 1-3 offline to confirm the notes are grounded; no label was changed because of a validator result.
- **Recommended before the paid run: a second person reads the labels** (especially the 32 natural answers and the 4 ambiguous cases, where "acceptable English" is a judgment) and any change is made before the hashes are recorded in an approval. The datasets are frozen by hash from the moment this PR merges: tests pin both hashes and the approval file binds them, so a later edit is impossible to miss. After the release decision they stay frozen; prompts and rules must not be tuned against their results first.

## 2. Runner extension (this PR)
- Stages run in a fixed order: **calibration**, then **held-out**. The approval's `stages` lists which are authorized.
- **Stage gate:** if the calibration stage lets any seeded unsafe pair through the pipeline, or the budget stops it, the held-out stage is skipped and nothing is spent on it.
- Calibration costs one comparison request per pair (39). The pipeline decision for a pair uses the production `coach` path with the verdict already obtained, never a second request.
- The approval additionally binds `calibration_sha256` and `stages`; `max_requests` must cover the selected stages and cannot exceed 400 in code. All existing safeguards are unchanged: $1.00 hard cap that an approval cannot raise, reservation before every request, fsync'd ledger, zero retries, concurrency one, 30 s timeout, family-based environment refusal (no `AUDLI_*`, Supabase, Postgres, database URL or other-provider variables), pinned model, credential read only after validation.
- Every report rate carries its sample size and exact 95% bound (`scripts/eval_statistics.py`), and the report ends with the **proposed** thresholds applied to the run (section 4). They are evidence for a decision; the report states they are not ratified and never says "approved".

## 3. Are the proposed 95% bounds achievable with these sample sizes?
Exact (Clopper-Pearson, one-sided 95%) upper bounds, and the chance a gate passes at a given *true* failure rate:

| measure | n | observed | upper 95% | note |
|---|---|---|---|---|
| unsafe leaks through the pipeline | 27 | 0 | **10.5%** | rule of three ≈ 3/n. Showing <5% needs 59 unsafe trials with 0 leaks; <1% needs 299 |
| false rejections (good candidate rejected) | ~64 | 6 | 17.7% | 7 of 64 gives a point estimate of 10.9%, over 10% |
| false rejections | ~64 | 4 | 13.7% | |
| unnecessary edits to natural answers | 32 | 3 | **22.5%** | even 2 of 32 gives 18.4% |
| unnecessary edits | 32 | 0 | 8.9% | |
| comparison wrongly rejects a same-meaning pair | 12 | 0 | 22.1% | too small to gate |

Probability the gate passes when the true rate is p:

| gate | p = 2% | 5% | 8% | 10% | 15% | 20% |
|---|---|---|---|---|---|---|
| false rejections ≤ 6 of 64 | 1.00 | 0.96 | 0.75 | 0.54 | 0.14 | 0.02 |
| unnecessary edits ≤ 3 of 32 | 1.00 | 0.93 | 0.75 | 0.60 | 0.27 | 0.09 |
| unnecessary edits ≤ 2 of 32 (bound ≤20%) | 0.97 | 0.79 | 0.52 | 0.37 | 0.12 | 0.03 |
| 0 leaks in 27 unsafe | 0.58 | 0.25 | 0.10 | 0.06 | 0.01 | 0.00 |

Conclusions, stated plainly:
1. **The proposed "point ≤ 10% and exact upper bound ≤ 20%" for false rejections is achievable at n ≈ 64**, and is the same as "at most 6 failures". It reliably passes a pipeline whose true rate is ≲ 5%, usually fails one at ≥ 15%, and **cannot reliably tell 10% from 15%**. A pass means "observed ≤ 9.4%, plausibly as high as 17.7%", not "the rate is below 10%".
2. **The same two-part rule is too strict for 32 natural answers** (it would allow only 2 failures, and a pipeline with a true 5% rate would fail it 21% of the time). Practical recommendation: gate on the point estimate (≤ 3 of 32, ≤ 10%) and report the 22.5% bound openly; if the owner wants the bound ≤ 20% to gate, add natural cases (≥ 60 natural answers allows 6 failures).
3. **Zero observed unsafe acceptances cannot be shown to be "rare" by this evaluation.** 27 seeded unsafe pairs bound the leak rate only below ~10.5%, and held-out v2 adds little (it contains unsafe candidates only if the model produces them). The evaluation is a necessary gate that can find defects; it is not a proof of safety. Accordingly, activation should be staged (internal learners first, rejection-layer monitoring already in place, a quick rollback via the flag), which is a decision for the owner and not something this PR changes.
4. The 12 same-meaning pairs are for diagnosis (are the five evaluation-1 patterns still rejected by the comparison step?), not for a pass/fail rate.

## 4. Recommended thresholds (applied automatically in the report, labelled PROPOSED)
| criterion | gate |
|---|---|
| held-out false accepts | 0 |
| seeded unsafe leaks (pipeline) | 0 of 27 (report the 10.5% bound) |
| false-rejection rate | point ≤ 10% **and** exact upper ≤ 20% (≤ 6 of ~64) |
| unnecessary-edit rate (natural answers, ambiguous excluded) | point ≤ 10% (≤ 3 of 32); bound reported, not gating |
| correction yield (fix cases accepted and correct) | ≥ 80% |
| mean best F1 of accepted corrections | ≥ 0.95 |
| provider failure rate | ≤ 2%, no retries |
| latency | p95 per improvement ≤ 5 s (evaluation 1: 3.3 s) |
| cost | mean ≤ $0.001 per attempt; the run stays under the $1 cap |
Informational, never gating: ambiguous-intent edits accepted; the seeded same-meaning results.
Any miss means: investigate each failing case individually and do not recommend activation. A pass means only that the evidence supports asking the owner for the next decision.

## 5. Revised evaluation plan (pinned, nothing executed)
| item | value |
|---|---|
| model | `gpt-4.1-mini-2025-04-14` (pinned; the returned model is recorded and must match) |
| prompt version | `v3` |
| held-out sha256 | `91bc9d23e319d3ff7d4dabeb1e56fa1ba8dc693fe3c06d35803efcae07bedffd` |
| calibration sha256 | `00eb8d51fb309c1d2e410af9e676b5f1a204df20922a7d096c314b16568fde2f` |
| requests | calibration 39 + held-out at most 300 (100 cases × coach, one repair, one comparison) = **at most 339** (code ceiling 400); expected about 39 + 195 ≈ 235 (evaluation 1 used 1.9 requests per case) |
| prices | $0.40 / $1.60 per million tokens (input/output); an approval must restate them |
| worst-case reservation (every request at its ceiling, every repair used) | **$0.9488**, under the cap |
| expected cost | roughly $0.07 (evaluation 1: $0.0209 for 99 requests; prompt v3 is longer and there are about 50% more requests) |
| hard ceiling | **$1.00**, enforced in code, not raisable by an approval; the run stops rather than exceeds it |
| retries | none (SDK `max_retries=0`, no resend after an ambiguous failure) |
| order | calibration first; held-out only if calibration leaked nothing and was not budget-stopped |
| alternative | `"stages": ["calibration"]` with `max_requests` ≥ 39 (worst-case reservation $0.0686) to measure the comparison step alone first |

## 6. Verification in this PR
Every existing adversarial meaning-preservation test remains in the suite and green (the full backend suite is run on the PR). Nothing in the validator or prompts changed; this PR touches evaluation support only.

## 7. Authorization requirements (the next boundary)
1. Owner reviews the labels in both datasets (recommended), then confirms the two hashes above.
2. Owner personally completes a copy of `docs/AUD-77-LIVE-EVALUATION-APPROVAL.json`: `approved` true, `approved_by`, `approved_at`, `expires_at`; optionally narrows `stages`. No other field may change. `python3 ~/audli-eval/launch.py validate` must print VALID.
3. A new dedicated OpenAI key (the previous one was deleted from disk and is to be revoked by the owner) in a mode-600 key file or a hidden prompt, with an organization spend limit.
4. Explicit "authorized" in the conversation. Until then no paid call is made, migration 007 stays unapplied, and the flag stays off.
