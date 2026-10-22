# AUD-77 live evaluation runner

**Status: implemented, NOT executed. No provider has been contacted and no credential has been read.** Execution needs a separate owner approval file that matches the exact plan in `docs/AUD-77-LIVE-EVALUATION-PLAN.json`.

## What it measures
- **Correction quality**: best token-F1 against reference rewrites, plus the required fixes present.
- **Meaning preservation**: fixture invariants (`must_preserve` / `must_not_add`) checked on the model's *raw* candidate, independently of the validator. A candidate the pipeline accepted that breaks an invariant is a `FALSE_ACCEPT`.
- **Pipeline agreement**: false accepts, false rejections, unneeded edits, and which layer (L1–L4) caught each violation.
- **Cost and latency**: actual reported token usage at the stated prices; p50/p95 per call and per improvement; provider errors.

## Plan (offline, `plan --repeats 1`)
| Item | Value |
|---|---|
| Model | `gpt-4.1-mini-2025-04-14` (pinned; the approval must name it exactly) |
| Prompt version | `v2` |
| Prices | $0.40 / 1M input, $1.60 / 1M output (verified 2026-10-22; the approval must repeat them) |
| Held-out cases | 55 synthetic ESL answers, 20 categories, disjoint from the development set |
| Sent to a provider | 51 (4 are blocked before any call: too short, too long, two low-confidence recognitions) |
| Maximum requests | 153 (51 cases × coach + one repair + one comparison); ceiling 400 |
| Worst-case reservation | $0.35 for the whole plan (every case, repair and comparison) against a $1.00 hard cap |
| Expected typical spend | about $0.02 (mock harness estimate ≈ $0.0004 per attempt) |
| Retries | none; SDK `max_retries=0`; concurrency 1; 30 s timeout |

Categories and every case's reference rewrites and invariants are listed in the plan JSON (`categories`, `expected_outputs`).

## Guarantees enforced in code
- Synthetic fixtures only. The module imports no storage code and refuses to run if a database or production variable is in the environment.
- The $1.00 cap is a constant an approval cannot raise. Each request's reservation is a ceiling, not an average: input is counted at one token per UTF-8 byte (a byte-level tokenizer cannot exceed that) plus a framing allowance, and output at the hard `max_output_tokens` cap (700 coach, 60 comparison). It is reserved first; the run stops rather than cross the cap. Usage that exceeds a reservation ends the run and is reported as `cap_overrun`.
- An fsync'd ledger line is written before every request. An existing ledger blocks a rerun; nothing resumes.
- A failed or ambiguous request is never resent; its reservation stays charged.
- The credential is read only after the approval validates. The approval is bound to model, prompt version, fixtures hash, prices, `max_usd ≤ 1`, `max_requests`, and an expiry.
- Learner text is not involved; stored output is the synthetic candidate only. `store=false` is sent, but that is not zero retention (OpenAI keeps abuse-monitoring logs up to 30 days).

## To run (only after the owner writes an approval)
```
python -m scripts.run_review_improvement_eval plan --repeats 1 --out plan.json
# owner copies the template from plan.json "approval_requirements", fills every field, sets "approved": true
OPENAI_API_KEY=... python -m scripts.run_review_improvement_eval run --approval approval.json --out runs/<run_id>
```
The template in the plan is deliberately not an approval and is refused as written.

## Interpreting results
Mock tests establish plumbing only. Acceptance of meaning-changing corrections must be **0** (any `FALSE_ACCEPT` blocks release); false rejections are a quality cost, not a safety failure. A 55-case set cannot prove a low rate: report it with its sample size.

The earlier `evaluate_review_improvement.py plan` is superseded for live execution by this runner.
