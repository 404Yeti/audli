# AUD-89 P1 — Idempotent recording upload (`AUDLI_UPLOAD_IDEMPOTENCY`, default off)

**Problem.** A recording that was sent but whose answer was lost (or that the browser sends again after a timeout) creates a second attempt and a second speech-to-text call. Before this change the server could not tell a repeat from a new recording.

**Change (exercise uploads, `POST /api/exercises/{id}/attempts`).** The browser already tags each recording with a random 16-hex `X-Audli-Upload-Id` that stays with the recording across sends (AUD-89 P0). With the flag on and a well-formed header, the attempt's primary key is derived, not random:

```
attempt id = UUIDv5(fixed namespace, "<learner id>|<exercise id>|<upload id>")
```

* A repeat finds the first attempt **before** admission, validation and transcription, and returns `{id, transcription}` unchanged with `X-Audli-Replayed: true`: no second attempt, no second STT call, no state transition, and it still works after the exercise was completed (the old check would answer 409).
* If the first request wrote the attempt but failed before its pending marker (`conversation.pending_attempt_id`), the replay completes the marker, exactly as the first request would have. It never touches the conversation if a newer recording is pending or the attempt already has a turn.
* Without the flag, without the header, or with a malformed or duplicated header: no code path changes; the id is a random UUIDv4 as before.
* Health reports `upload_idempotency`.

## Why the primary key, not a JSON field
The brief allowed storing the upload id inside the attempt's JSON provided the account lease guarantees uniqueness. Two reasons not to:

1. **Rollback safety.** `Transcription` (and every stored snapshot) is a strict model that rejects unknown keys. A row written with an extra `upload_id` key would make a release *without* this code fail on `assess` for that attempt. A derived id adds nothing to any row: a rollback reads everything this code ever wrote.
2. **Uniqueness does not depend on the lease.** A JSON field needs a lookup-then-insert that is only safe while something serializes the two steps. The primary key is enforced by the database itself (`INSERT … ON CONFLICT (id) DO NOTHING RETURNING id`; on PostgreSQL a concurrent second insert waits for the first transaction, on SQLite writers are serialized by `BEGIN IMMEDIATE`).

No column, index or migration is needed, so the schema-bridge process is not involved.

### What the lease and locks still contribute (production configuration: one Render instance, Supabase PostgreSQL, Supabase Auth)
Every mutating request of a learner holds the PostgreSQL advisory **account lease** for its whole life, plus the per-learner in-process lock `turn.upload`. Duplicates of one upload therefore queue (`test_duplicates_queued_behind_the_account_lease_create_one_attempt`), the second finds the first attempt when it gets its turn and never calls STT. The primary key is the safety net if either were ever absent (local development without auth, a second instance): `test_the_primary_key_alone_prevents_duplicates_with_no_lock_and_no_lease` runs 8 threads on separate connections with no application lock, on SQLite and PostgreSQL.

## Security
The id includes the learner id, so a learner can neither derive nor reach another learner's attempt, and `add_attempt_once` refuses an exercise the learner does not own (no insert, no read). The same upload id sent for a different exercise is simply a new attempt, as it is today. The header carries no account information (random per recording).

## Not covered yet (follow-ups; none changes behaviour today)
* `POST /api/lessons/{id}/attempts` (check-in / personal answer): the transcription is stored inside the lesson checkpoint JSON, so a safe upload id there needs the schema bridge. A duplicate already ends in the existing 409 "Reload your current lesson checkpoint" (the revision check), never in a duplicate state transition; the client recovers through `lessons/start`, which returns the saved `pending` transcription.
* Plan check-in, onboarding and homework uploads.

## Rollout
Merge (flag off, no behaviour change) → deploy → `AUDLI_UPLOAD_IDEMPOTENCY=true` on Render (one variable) → verify health shows `upload_idempotency: true` → one lesson. The header is only sent by a client built with `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1` (P0); until P2 ships the client does not resend, so the flag has no visible effect in production besides the safety net for the current "Retry conversation" path (which resends the retained recording). Rollback: unset the variable (about 2 minutes). Rows written with the flag on are ordinary attempts with ordinary UUIDs and need no repair.

Success: `upload.replayed` appears in `Latency summary` only when a repeat happened; duplicate attempts per exercise 0; STT calls per accepted upload 1.
