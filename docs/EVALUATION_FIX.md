# Exercise #3 evaluation failure

The stored failed attempt is `035dc1b7-5ff8-4b8a-85f1-6b38f899e7ab`. Live replays on isolated copies reproduced:

- Operation: `ComprehensionEvaluator.score_judgments`
- Exception: `builtins.ValueError`
- Message: `Evaluation cited evidence absent from learner transcript`
- Origin: `app/evaluation.py`, evidence-membership validation.

OpenAI parsed a response that met the old `EvaluationJudgments` schema, which allowed any evidence string up to 1000 characters. The subsequent application check required non-missed evidence to occur in the learner's transcript after normalization. That stronger constraint existed only in application code and prompting, causing intermittent failures. Two of three baseline live replays failed at this exact boundary; one succeeded.

The original browser terminal log was not persisted. Replays use the stored STT text, because failed confirmation edits are not saved. This reproduces the failure on the reported attempt, rather than recovering the exact historical provider response. Neither the learner text nor provider quote is printed or checked into a fixture.

## Fix

The provider now receives a per-attempt strict schema:

- Required object slots identify every expected information unit exactly once.
- Non-missed evidence is an enum of supplied verbatim learner excerpts.
- Missed evidence must be the empty string.
- Excerpts preserve learner wording, including grammatical errors. Long input is partitioned into bounded excerpts; generated values are never clipped or repaired.
- Provider slots are converted to the existing domain judgment list. Existing evidence, coverage, normalized score, and adaptation validation remain in force.

This uses required object keys, literal enums, and nested `anyOf`, supported by [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs). The schema constrains evidence provenance; semantic correctness remains the evaluator's responsibility.

Operation diagnostics now distinguish provider evaluation, scoring, adaptation, and persistence. Pydantic literal-error messages are redacted because their allowed values can include sensitive learner excerpts. No audio, keys, headers, or transcripts are logged.

## Verification

Commands from repository root:

```bash
source .venv/bin/activate && pytest -q
source .venv/bin/activate && pytest -q tests/test_evaluation.py tests/test_openai_provider.py tests/test_diagnostics.py --tb=short
source .venv/bin/activate && pytest -q tests/live_evaluation.py -s --tb=short --log-cli-level=ERROR
```

- Full backend suite: 95 passed.
- Focused evaluator/provider/diagnostics suite: 47 passed.
- Live evaluator: three consecutive corrected-provider replays passed, including persistence and idempotent repeated POST verification. All writes went to temporary database copies.
- Regression tests recreate an old-schema-valid fabricated quote and verify the unchanged application validator rejects it and the corrected provider schema rejects it earlier.
- Additional tests cover required units, empty/missed evidence, bounded verbatim excerpts, private literal-error logging, and safe retry after invalid evaluator output.

The original local database still has two evaluation/adaptation records. The failed attempt remains `transcribed`, without a saved result or applied adaptation. Its exercise is still current. A successful retry commits evaluation, adaptation, and learner state atomically. Repeating an already successful evaluation returns the saved result without advancing again.

## Product-owner retry

With the backend reloaded, keep the current browser attempt open, verify/correct its transcription, confirm it, and click **See what I understood** again. No new recording is required while that attempt remains in the page state. Expect evaluation POST 200, feedback, and one adaptation event. If the browser was refreshed, its unsaved attempt UI state may be gone; the backend attempt remains retryable using the same endpoint and confirmed text.

No frontend or unrelated product features changed.
