# AUD-87 — Lazy pool pre-ping (AUDLI_DB_LAZY_PING, default off; AUDLI_DB_LAZY_PING_SECONDS=30)

## Problem
`pool_pre_ping` sends a round trip before **every** checkout. A lesson makes about 150 checkouts: about 11 s of pings at 72 ms, nearly all of them on connections returned to the pool milliseconds earlier.

## Change (flag on, PostgreSQL only)
`app/lazyping.py` records when each connection was returned to the pool and answers the dialect's ping locally for connections idle less than the window; older ones are pinged as before, so connections that died while the pool sat idle are still replaced. Installed after the timing instrumentation, so `db.pool_ping` then counts real pings only.

## Proof (`tests/test_lazy_ping.py`, PostgreSQL)
Off: one ping per checkout; on: at most 3 in 12 requests; a connection idle past the window is pinged again; a connection killed while idle is replaced by that ping (requests keep succeeding); results are identical; installing twice is harmless; SQLite untouched.

## Measured (baseline 18.7 s sequential / 51.1 s with 4 learners)
Sequential turn p50 **15.7 s** (-3.0 s), p95 17.6 s; concurrent p50 42.2 s, p95 45.6 s.

## Risk and rollback
A connection that dies inside the window fails its first statement once (same as one dying mid-request today); SQLAlchemy then discards it. Rollback: unset the flag or revert.
