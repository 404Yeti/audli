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
| Worst-case reservation | $0.40 for the whole plan (every case, repair and comparison) against a $1.00 hard cap |
| Expected typical spend | about $0.02 (mock harness estimate ≈ $0.0004 per attempt) |
| Retries | none; SDK `max_retries=0`; concurrency 1; 30 s timeout |

Categories and every case's reference rewrites and invariants are listed in the plan JSON (`categories`, `expected_outputs`).

## Guarantees enforced in code
- Synthetic fixtures only. The module imports no storage code and refuses to run if a database or production variable is in the environment.
- The $1.00 cap is a constant an approval cannot raise. Each request's reservation is a ceiling, not an average: input is counted at one token per UTF-8 byte (a byte-level tokenizer cannot exceed that) plus a framing allowance, and output at the hard `max_output_tokens` cap (1,000 coach, 60 comparison; applied only by this runner, production coaching sets no cap). It is reserved first; the run stops rather than cross the cap. Usage that exceeds a reservation ends the run and is reported as `cap_overrun`.
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

## The prepared approval file (not an approval)
`docs/AUD-77-LIVE-EVALUATION-APPROVAL.json` is the approval file, filled in and bound to this exact plan: model `gpt-4.1-mini-2025-04-14`, prompt `v3`, the fixtures hash, the verified prices, `max_usd` 1.0, `max_requests` 153, `repeats` 1. It is committed with `approved: false` and the owner's four fields blank, so **it authorizes nothing and the runner refuses it as written**. A test pins that, and also that filling in only `approved`, `approved_by`, `approved_at` and `expires_at` makes it valid, so nothing else can drift unnoticed (change a fixture or a price and the test fails until the file is regenerated).

To authorize the run, the owner completes a COPY of the file outside the repository (the committed file stays unapproved):
```
mkdir -p ~/audli-eval && chmod 700 ~/audli-eval
cp docs/AUD-77-LIVE-EVALUATION-APPROVAL.json ~/audli-eval/approval.json && chmod 600 ~/audli-eval/approval.json
```
Edit only these four fields in `~/audli-eval/approval.json` and leave every other field exactly as it is:
- `"approved": true`
- `"approved_by"`: your name
- `"approved_at"`: the current time, ISO-8601 with a zone, for example `2026-10-23T09:00:00Z`
- `"expires_at"`: a later time with a zone, for example `2026-10-23T21:00:00Z` (a run after this time is refused)

Check the completed file. This reads no credential, makes no network call and creates nothing; it exits 0 and prints `VALID` only if the file matches the exact plan (model, prompt, fixtures hash, prices, `max_usd` 1.0, 153 requests) and your shell has no database, Supabase, `AUDLI_*` or other-provider variable set:
```
.venv/bin/python -m scripts.run_review_improvement_eval validate --approval ~/audli-eval/approval.json
```
Only after it prints `VALID`, run it in a clean environment, using a dedicated OpenAI key (variable name `OPENAI_API_KEY`, not `AUDLI_OPENAI_API_KEY`) whose organization has a spend limit of a few dollars. Do not paste the key into chat; type it into your own shell:
```
env -i HOME="$HOME" PATH="$PATH" OPENAI_API_KEY="..." .venv/bin/python -m scripts.run_review_improvement_eval run --approval ~/audli-eval/approval.json --out ~/audli-eval/run-1
```
`run` repeats every check itself before it reads the key or builds a client, and refuses an output folder that already holds a ledger.

Safeguards that apply regardless of the file: synthetic fixtures only; no database import; refuses to start if any database, Supabase, application (AUDLI_*) or other-provider variable is set; a hard $1 cap against a worst-case token ceiling (whole plan $0.40); no retries; one request at a time; every request is written to disk before it is sent. Nothing has been run.

## Interpreting results
Mock tests establish plumbing only. Acceptance of meaning-changing corrections must be **0** (any `FALSE_ACCEPT` blocks release); false rejections are a quality cost, not a safety failure. A 55-case set cannot prove a low rate: report it with its sample size.

The earlier `evaluate_review_improvement.py plan` is superseded for live execution by this runner.
