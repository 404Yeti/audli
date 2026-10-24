# AUD-87 — Lazy pool pre-ping (AUDLI_DB_LAZY_PING, default off; AUDLI_DB_LAZY_PING_SECONDS=5)

## Problem
`pool_pre_ping` sends a round trip before **every** checkout. A lesson makes about 150 checkouts: about 11 s of pings at 72 ms, nearly all of them on connections returned to the pool milliseconds earlier.

## Change (flag on, PostgreSQL only)
`app/lazyping.py` records when each connection was returned to the pool and answers the dialect's ping locally for connections idle less than the window; older ones are pinged as before, so connections that died while the pool sat idle are still replaced. Installed after the timing instrumentation, so `db.pool_ping` then counts real pings only.

## Proof (`tests/test_lazy_ping.py`, PostgreSQL)
Off: one ping per checkout; on: at most 3 in 12 requests; a connection idle past the window is pinged again; a connection killed while idle is replaced by that ping (requests keep succeeding); results are identical; installing twice is harmless; SQLite untouched.

## Measured (baseline 18.7 s sequential / 51.1 s with 4 learners)
Sequential turn p50 **15.7 s** (-3.0 s), p95 17.6 s; concurrent p50 42.2 s, p95 45.6 s.

## Failure behaviour (measured, `tests/test_lazy_ping_recovery.py`, PostgreSQL)
* Every backend killed while connections are inside the window: **one** request gets a 503 (its first statement hits a dead connection), the next four succeed, nothing stays checked out. With the flag off the same event is invisible (pre-ping replaces the connection first). That single transient 503 per connection-loss event is the price of the saving; the window is therefore 5 s by default (not longer), which still skips almost all pings inside a turn and pings each connection again after the learner's pause.
* A write whose connection dies before its transaction starts is all-or-nothing: either a 503 with the stored profile unchanged, or success; the retry succeeds. Repeated loss of all backends never wedges the pool.
* Connections idle longer than the window are pinged and replaced silently, exactly as before.

## Risk and rollback
A connection that dies inside the window fails its first statement once (same as one dying mid-request today); SQLAlchemy then discards it. Rollback: unset the flag or revert.
