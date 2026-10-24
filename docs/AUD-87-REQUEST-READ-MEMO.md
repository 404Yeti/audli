# AUD-87 — Request-local read memo (AUDLI_REQUEST_READ_MEMO, default off)

## Problem
Handlers read the same rows repeatedly inside one request (measured over a lesson: the exercise row 24 times, current exercise 11, conversation 9, attempt 8), each a checkout and a statement at 72 ms.

## Change (flag on)
For the pure single-learner reads `profile`, `current_exercise`, `exercise`, `attempt`, `completed`, `result`, `conversation`, `coach_audio`, `lesson`, `lesson_for_exercise`, a repeat call with the same arguments inside one request is answered from a per-request memo. **Any other repository call drops the whole memo** (before and after it; a `transaction()`/`read()` block also on entry and exit), callers always receive deep copies (handlers mutate what they read), exceptions are not remembered, unhashable arguments just read, and the memo is created per request and never shared.

## Proof (`tests/test_request_read_memo.py`)
Unit tests with a fake repository (single read, copy isolation, invalidation by writes, other reads and transaction blocks, failures, per-request scope) and a whole lesson on SQLite and PostgreSQL: identical responses and identical rows in every table, never more statements on any route, fewer on the assessment route.

## Measured (baseline 18.7 s / 51.1 s)
Assessment route 48 to 45 statements and 17 to 14 checkouts; sequential turn p50 **18.0 s** (-0.7 s). A small but safe saving; its ceiling is 4.2 s per lesson (`docs/AUD-87-REDIS-EVALUATION.md`).

## Rollback
Unset the flag or revert.
