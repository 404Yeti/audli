# AUD-87 — Production rollout log

## Step 1: `AUDLI_DB_OFFLOAD=true` (approved 2026-10-24)

### Recorded before the change (09:41 UTC)
| Item | Value |
|---|---|
| Service | Render `audli-api` (`srv-db270erbc2fs73fg85gg`), Starter `0.5c-512mb`, Singapore, 1 instance, auto-deploy on `main`, health check `/api/health` |
| Live deploy | `dep-dbe7nto473hc738qh0b0`, commit `8b1e2294de6ae5e30ba800c36e23d74dca900478` (#99), healthy, no error or warning logs since the previous deploy |
| `/api/health` | `status ok`, `db_read_autocommit {requested true, effective true}`, `db_state_only_transitions false` |
| Environment variables changed by us during this work (values contain nothing secret) | `AUDLI_DB_READ_AUTOCOMMIT=true` (03:12 UTC), `AUDLI_TURN_TRACE=true` (05:17 UTC); `AUDLI_LATENCY_AGGREGATES=true` and the Supabase/OpenAI/ElevenLabs/database settings pre-existed and are untouched. Secrets are never copied here. Not set: `AUDLI_DB_STATE_ONLY_TRANSITIONS`, `AUDLI_DB_LAZY_PING`, `AUDLI_LEASE_POOL`, `AUDLI_ACK_LEASE_EXEMPT`, `AUDLI_REQUEST_READ_MEMO` |
| Production baseline for comparison | owner class 04:17 UTC: `loop.lag` mean 20 to 33 ms, max 3.0 to 4.5 s per 5-minute window; speech-end-to-coaching mean 26.9 s (n=9); ack-audio request 8 s; `auth.lease` max 7.6 s |

### Change
`AUDLI_DB_OFFLOAD=true` merged into the service environment (no other variable) at 09:42:04 UTC; deploy `dep-dbe7qr2vcj2c73c1dt80`, same commit, live at 09:43; health 200; no error or warning logs.

### Rollback (any time, about one minute)
Render dashboard or API: set `AUDLI_DB_OFFLOAD` to `false` (or delete it); the service redeploys the same commit with the previous code path (`Offload.run` calls the function inline). If code itself were suspect: redeploy the previous live commit `8b1e229` from the Render deploy history (the flag-off build is behaviourally identical to it). No data migration is involved, so rolling back never needs data repair.

### Rollback triggers
Any 5xx or `Operation failed` that the previous class did not have; `QueuePool limit` or other pool timeouts; any request that hangs; a learner state that does not match what was said; `loop.lag` max over 1 s or mean over 50 ms in a window with traffic; speech-end-to-coaching worse than the 26.9 s baseline by more than 10 %; health check failures.

### Acceptance checks
1. Deploy healthy, startup log clean (done).
2. During the controlled owner class: `Latency summary` windows show `loop.lag` mean and max far below the baseline, `/api/health` latency in the window under 100 ms, no error-level logs, no pool errors, lease attempts unchanged or lower.
3. Cancellation: the owner closes the tab or locks the phone in the middle of an assessment, reopens, and continues; expected: the turn resumes from the saved checkpoint, no "saving another step" lockout lasting beyond the assessment, no error logs.
4. Connection cleanup: no pool exhaustion errors over the class; `db.checkout` mean stays near the 72 ms ping; after the class the idle windows show no stuck work (`req.write.*` counts match completed requests).
5. Data consistency: the lesson history and results show the same turns and feedback the learner heard.
6. Measured: speech-end-to-coaching (`client.turn_to_playback`) compared with the 26.9 s baseline.

Results of the owner class are recorded in the next section when it has run.

## Lazy ping (`AUDLI_DB_LAZY_PING`): acceptable error threshold (to apply before step 2)
Known behaviour (`docs/AUD-87-LAZY-PING.md`, measured): a database connection that dies inside the 5-second window costs exactly one 503 and the pool heals; with the flag off the same event is invisible. The events that kill connections are rare: a Supabase pooler restart or maintenance, a network blip, or a Render restart (which replaces the pool anyway). Idle-timeout deaths are outside the window and are still caught by the ping.

Proposed acceptance threshold for enabling it and for keeping it on, measured on `Latency summary` / Render logs:
* at most 1 in 1,000 API requests may fail because of a dead connection, over any 48-hour period;
* never two consecutive failures for the same learner, and never two failures within 60 seconds of each other (a cluster means the pool is not healing);
* every failure must be correlated with a known connection event (deploy, Supabase incident); an unexplained cluster is an immediate rollback.
Before step 2 it is also worth shortening the exposure further if the first class shows any failure: set `AUDLI_DB_LAZY_PING_SECONDS=2` (the saving is nearly the same because the connection is reused within a few hundred milliseconds inside a turn).
