# Live exercise-generation failure — 2026-10-05

## Reproduction and exact cause

Using the configured live OpenAI provider and a copy of the current learner profile in an isolated temporary database, POST /api/exercises returned 503.

Development diagnostics identified:

```text
Operation failed: ExerciseGenerator.generate
builtins.ValueError: Generated information density outside target:
density=1, expected_details=3..3, actual_details=4
```

The exception came from the information-density check in `OpenAIProvider.generate`, after Responses structured parsing and Pydantic validation succeeded. Speech rate, duration and vocabulary matched the requested values. The failure occurred before speech generation.

The original provider schema allowed `important_details` arrays of length 3–8 for every density, while application code required exactly 3 at density 1. OpenAI returned 4, valid against the supplied schema but invalid against the application's density policy. Prompting for three details did not enforce the policy.

## Fix

Generation now builds a Pydantic schema derived from ExerciseContent with density-specific important_details array constraints:

- Density 1: minItems=3, maxItems=3.
- Density 2: minItems=4, maxItems=5.
- Density 3: minItems=6, maxItems=8.

The OpenAI Responses API receives those constraints as part of its strict structured-output contract. Existing application checks remain in place; no detail is truncated, no mismatch is silently accepted, and no ValueError is suppressed. Difficulty metadata, enum values and adaptation logic remain unchanged.

## Development observability

`AUDLI_ENVIRONMENT=development` enables operation names, qualified exception types, sanitized messages and every traceback frame (filename/function/line). No locals or source snippets are included, since either can contain literal credentials or audio. Pydantic validation errors omit input values/context. Known environment/configuration secrets, bearer/authorization values, named secret assignments and binary payload representations are redacted.

`AUDLI_ENVIRONMENT=production` logs only operation and exception type. Client responses remain generic in both modes. The exercise route identifies generation, speech generation and audio sanitization separately; structured parsing identifies its schema.

## Verification

```bash
source .venv/bin/activate && pytest -q
source .venv/bin/activate && pytest -q tests/live_generation.py -s
.venv/bin/python -m compileall -q app tests scripts
```

Full backend suite after the fix: **87 passed in 32.83 seconds**. Python compilation also passed.

Live check after the fix: **1 passed in 31.31 seconds**. POST /api/exercises returned **200**, generated speech was sanitized and persisted, audio GET returned **200**, and the transcript gate returned **403**. This used real generation and speech APIs through the application route with ASGI transport; it did not exercise browser playback or recording.

The opt-in live file is not discovered by the normal backend suite; invoking it explicitly incurs API usage. It copies the learner profile into temporary storage to avoid returning a cached exercise or changing current learner progress. No API key, real learner audio or original listening script was printed. No full V0.1 completion claim is made.

Regression coverage checks the exact four-details/density-one failure and inspects the strict JSON schema actually sent through the real SDK for all three density levels. Diagnostics tests cover type/message/stack/operation visibility, secret and binary-payload redaction, Pydantic input omission, generic client errors and production behavior.

## Bounded script-length recovery

A later live failure returned 50 words for a target of 84, below the existing 54.60–117.60 tolerance. `OpenAIProvider.generate` now makes at most two structured-generation calls: the initial call and one repair call, only when script word count is the sole failed validation.

Schema parsing, requested difficulty, and detail-count checks run before the length decision on both candidates. Other errors propagate immediately. The initial prompt specifies the permitted whole-word range; the repair prompt says whether the previous script was too short or too long, gives its count, and requests a complete regenerated exercise within the same range. For target 84, this means 55–117 words. The original numerical tolerance is unchanged. No generated prose is truncated, padded, or repaired in application code.

Development diagnostics record attempt number, target, allowed bounds, actual count, reason, and whether another attempt is scheduled. Rejected counts log at WARNING; valid candidate counts log at INFO. No script or profile content is logged. If the second candidate also fails, the same length ValueError propagates and the API retains its existing generic 503 behavior. The content-repair limit is independent of the SDK's existing bounded transport retry policy.

Regression coverage includes short→valid, long→valid, valid→no retry, two invalid candidates, unrelated first/second-candidate errors, integer boundaries, safe metadata logging, and an exhausted-retry API request preserving the previously completed exercise and learner progress.

Commands:

```bash
source .venv/bin/activate && pytest -q tests/test_openai_provider.py --tb=short
source .venv/bin/activate && pytest -q
```

These regression checks use the real SDK with mocked provider HTTP responses so both failure directions can be reproduced deterministically. No additional live API calls were made for this change.

Complete backend result after the bounded-retry change: **111 passed in 36.17 seconds**.
