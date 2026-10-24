# AUD-87 — Is Redis worth it? (evaluation against measurements)

Question: would a shared cache such as Redis remove enough database round trips to be worth its cost and risk, compared with request-local caching or connection reuse?

## What is repeated (measured, `scripts/analyze_repeated_reads.py`)
One synthetic lesson over the real app on PostgreSQL: 25 requests, 152 pool checkouts, 288 statements = 440 round trips with the P2 autocommit setting (one ping per checkout plus one per statement). Each read block (a checkout whose statements are all `SELECT`s) was replayed against an idealised cache with perfect invalidation: a table's version bumps on every write to it, unlimited TTL, zero cache latency.

| Cache | Read blocks avoided | Round trips avoided | Time per lesson at 72 ms |
|---|---|---|---|
| Request-local (same request only) | 29 of 152 checkouts | 58 of 440 (13.2 %) | 4.2 s |
| Shared across requests (Redis, best case) | 85 of 152 checkouts | 170 of 440 (38.6 %) | 12.2 s |

The reads that repeat inside a single request are few and specific (count over the lesson): the `exercises` row 24 times, the "current exercise" lookup 11, the conversation row 9, the attempt row 8, the audio-asset owner check 4, the evaluation/adaptation result 3. They are the same handful of rows read again by `exercise_or_404`, `current_exercise`, `public_conversation` and the cue builders.

## Which reads could plausibly hit a shared cache
Only reads of data that does not change between turns. Almost all the repeated rows are lesson state (conversation, attempts, exercise progress) that **every turn writes**: the 38.6 % best case already assumes an invalidation on every write and that no other process writes. Realistic hit rates are lower: each write path (more than twenty methods across nine tables) would need a correct invalidation, a missed one shows a learner stale state in a product whose rules forbid wrong scores or early answers, and a Redis outage or lag would add a second failure mode to every turn. A cache hit still costs a Redis round trip (1 to 3 ms in the same region) and serialisation; a miss adds that to the database cost.

## What is simpler and gives most of the same saving
| Option | Measured / estimated saving | Risk |
|---|---|---|
| Lazy pool ping (`AUDLI_DB_LAZY_PING`) | **measured**: sequential turn p50 18.7 s to 15.7 s (about 3 s); removes about 150 pings (11 s) per lesson | a connection dying inside the 30 s window fails one request once |
| Request-local read memo (`AUDLI_REQUEST_READ_MEMO`) | **measured**: assessment route 48 to 45 statements and 17 to 14 checkouts; sequential p50 18.7 s to 18.0 s; ideal ceiling 4.2 s per lesson | none beyond one request; callers get copies; any other repository call drops the memo |
| One connection per request instead of one per call | estimate 80 fewer pings per lesson (about 6 s), mostly overlapping lazy ping | restructures transactions; not needed once pings are lazy |
| In-process TTL cache of the Supabase Auth verification (30 to 60 s) | estimate 0.3 s per POST, about 1.2 s per turn; needs its own approval (see PR #84) | revocation window; already designed |

## Conclusion
Do **not** adopt Redis now. The ideal shared-cache ceiling (12 s per lesson) is about what lazy ping already delivers (11 s of pings) without new infrastructure, a new failure mode or an invalidation surface over lesson state, and the remaining repeated reads are mostly removed by the request-local memo. Reconsider only if, after the offload, lease and ping fixes, measurement still shows database reads of rarely-changing data (profile, plan, tutor voice, eligibility) dominating: those are the only candidates where a shared cache is both safe and effective, and an in-process TTL cache would serve them on a single instance anyway.
