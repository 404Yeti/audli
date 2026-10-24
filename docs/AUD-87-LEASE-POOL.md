# AUD-87 — Pooled, one-statement account lease (AUDLI_LEASE_POOL, default off)

## Problem
Every mutating request (and every retry while waiting) opened a **new** connection to Tokyo (435 ms mean), ran two statements (218 ms) and closed it (72 ms): about 0.7 s per lease attempt, four times per turn, synchronously on the event loop.

## Change (flag on)
The lease engine becomes a small `QueuePool` (4 kept, 32 overflow, 240 s recycle, no pre-ping). One attempt is `SELECT pg_try_advisory_xact_lock(:key), set_config('idle_in_transaction_session_timeout','0',true)`: the **same lock and key**, the same transaction-local setting, one statement instead of two on a kept connection. The lock is still released by ending the transaction when the connection is returned. A kept connection that died is detected by the statement itself, discarded (never returned to the pool) and replaced once. With the flag off the code path is the previous NullPool one.

## Proof (`tests/test_lease_pool.py`, PostgreSQL)
Pool type switches with the flag; one statement and no new connection per lease (12 statements and 6 physical connections off, 6 and at most 2 on); an external holder of the same key still excludes the request and the key is free afterwards; same-account requests serialise while another account is unaffected; a killed kept connection is replaced without failing the request; a failing handler releases the lease; exempt and read requests still take none. The existing every-mutating-route lease test is unchanged.

## Measured (baseline 18.7 s / 51.1 s)
Sequential turn p50 **17.2 s** (-1.5 s), p95 19.5 s, upload 4.2 to 3.9 s, coaching 3.2 to 2.9 s; concurrent p50 42.9 s (p95 57.7 s).

## Risks and rollback
More long-lived database connections (4 kept, up to 36 under load): check the Supabase pooler limit before enabling. Rollback: unset the flag or revert.
