# AUD-88 — Where the ~100 database round trips per turn go, and how to remove them (plan, from real query traces)

Method: `scripts/trace_route_queries.py` runs the synthetic lesson over the real app on PostgreSQL and records every checkout and statement per request (counts, not time). Cost model = production with P2 autocommit reads and lazy ping: a read statement is 1 round trip (RT), a write transaction is BEGIN + its statements + COMMIT. 1 RT = 72 ms (Singapore to Tokyo). Authentication, the lease and the scope check add about 4 to 5 RT per POST outside these traces.

## The traced cost of a turn
An exercise has two turns (summary, then one follow-up). Per route, last call = the final turn:

| Route | RT (first turn) | RT (final turn) | Reads | Write transactions (statements) | Statements that rewrite the conversation family |
|---|---|---|---|---|---|
| `POST /exercises/{id}/attempts` (upload) | 14 | **27** | 5 | 2 (18) | 13 |
| `POST /attempts/{id}/assess` | 35 | **69** | 19 | 2 (46) | 28 |
| `POST /exercises/{id}/coach-audio` | 13 | 17 | 10 | 1 (5) | 0 |
| `POST /exercises/{id}/conversation/ready` | | **33** (once per exercise) | 13 | 1 (18) | 15 |
| `POST /exercises/{id}/conversation/listened` | | 13 (once) | 9 | 1 (2) | 1 |
| `GET /exercises/{id}/conversation` | | 11 | 11 | 0 | 0 |

Turn totals: first turn 62 RT (4.5 s), final turn 113 RT (8.1 s), plus ~13 RT of per-request overhead, so about 100 RT (7.2 s) per turn on average, which is the ~7 s the benchmark leaves after the stubbed providers.

## What the statements are doing (final-turn assessment, 69 RT)
1. **5 reads before any write** (`attempt`, `conversation`, `attempt` again, `exercise`, `lesson`).
2. **Write 1 (prepare_assessment): 18 statements.** To record "assessing this answer" it rewrites the whole conversation: upserts the row, then for every follow-up, turn, assessment and evidence unit a `SELECT` of the prior state and an `INSERT ... ON CONFLICT DO NOTHING`. None of the children change; only the conversation state and the attempt do.
3. **4 reads** for adaptation (profile, lesson, lesson exercises, work).
4. **Write 2 (complete): 28 statements.** The same full rewrite again (about 15 statements) plus the genuinely new rows: evaluation, adaptation event, profile update, the new turn, its assessment and evidence units.
5. **10 reads to build the response** (`public_conversation`): conversation, attempt, result, then the same three again (the second pass repeats the first; the request memo removes part of this), then exercise and lesson lookups.

The same shape recurs: upload in the final turn rewrites the conversation again (13 statements to change a state flag and a pending attempt id), and `ready` rewrites it a fourth time (15 statements to change `state`).

## Plan, in order of saving per risk (all flagged, each validated with the golden-dump technique and the benchmark)
| # | Change | Where | RT saved per exercise (2 turns) | Risk |
|---|---|---|---|---|
| 1 | **State-only conversation persistence** wherever only the state changes: `ready` (already built as P5, off), upload's pending-attempt marker, `prepare_assessment`'s checkpoint. One `UPDATE conversations` instead of the full rewrite | upload turn 2: -12, assess checkpoint turn 2: -15 and turn 1: -2, ready: -14 (P5 measured 31 to 14 statements) | **about 43** | low: same proof as P5 (identical rows in every table, fallbacks to the full save) |
| 2 | **One "turn context" read** replacing the 3 to 9 separate pre-reads (`exercise`, `current_exercise`, `completed`, `attempt`, `conversation`, `lesson`) of each hot route | assess -7, upload -3, coach-audio -7 per turn; listened -7; ready -2 | **about 34** | medium: new SQL; equivalence tests per route; must keep each owner-scoped check |
| 3 | **One read for the response** (`public_conversation`: conversation + pending attempt + result + exercise content) | assess -8 per turn, ready -9, listened -6 | **about 31** (overlaps with the request memo's 3) | medium: new SQL; the gate that hides scripts and answers must stay in one place |
| 4 | **Incremental child writes** in `complete`/the follow-up save: insert only the new follow-up, turn, assessment and evidence rows, not the whole family | assess turn 2: -11, turn 1: -5 | **about 16** | medium to high: idempotency and ordering of children; keep `ON CONFLICT` semantics |
| 5 | **Multi-row inserts / driver pipeline** for the evidence units | 5 to 6 inserts become 1 per write that creates a turn | **about 15** | low gain per risk; last |

Sum 139 RT per exercise; allowing 25 % overlap between 2/3/memo and 1/4, plan on about 100 RT per exercise, **about 50 RT (3.6 s) per turn**. Against the benchmark's 13.1 s this puts the server-observed turn at about **9.5 s**, below the 10 s target. Per-request overhead (auth verification 0.3 s x3, scope check, lease) is not counted and is the next 1 to 2 s (auth cache needs separate approval).

## Validation approach
Each step is its own flag (`AUDLI_DB_STATE_ONLY_TRANSITIONS` exists; the others new), proven equivalent exactly like P5: a deterministic lesson with the flag off and on, identical responses and an identical dump of every table, plus fallbacks and concurrency tests on SQLite and PostgreSQL, then measured with `scripts/bench_turns.py`. Step 1 can start immediately; steps 2 to 4 need the traces above as their statement-level specification.

## Not in this plan
Changing the database region (the biggest single lever: up to about 6 s per turn) and Redis (see `docs/AUD-87-REDIS-EVALUATION.md`).
