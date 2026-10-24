# AUD-87 — Acknowledgment cue without the account lease (AUDLI_ACK_LEASE_EXEMPT, default off)

Implements option B of `docs/AUD-86-ACK-LEASE-EXEMPTION-DESIGN.md` (PR #91).

## Problem
`POST /api/recognition/acknowledgment-audio` took the per-account Postgres lease, which `/assess` holds for its whole duration, so the cue came back only after the assessment (p50 10.1 s in the benchmark, 7.5 s in production), delayed the coaching request, and its lease polling blocked the event loop.

## Change (flag on)
* `LearnerAuthentication` skips the lease for exactly `POST /api/recognition/acknowledgment-audio`, after authentication succeeded (`ACK_ROUTE`; the reviewed `LEASE_EXEMPT_*` sets are unchanged).
* The handler becomes read-only: one `profile()` read picks the voice; the audio comes from `AckCache`, a process-level, learner-independent, single-flight, bounded (64) memory cache keyed by (voice signature, cue text). No `audio_assets` write. A cache miss is admitted against the learner's allowance (`limits.admit`) exactly like a billable miss; a fallback-voice result is never cached.

## Behavioural difference to know about
The cue audio is now shared between learners of the same voice (fixed neutral phrases, no learner data), where before each learner had a private database copy. `tests/test_tutor_tts.py::test_owned_api_cache_config_invalidation_and_transcript_gate` asserts the old per-learner behaviour and (rightly) fails if the flag is forced on for the whole suite; the same holds for tests that use the cue route as "an ordinary mutating route". With the flag off nothing changes.

## Proof (`tests/test_ack_lease_exempt.py`, SQLite and real PostgreSQL)
Cue served while the lease is held and other POSTs still queue; flag off still queues; only SELECTs run and every table is identical before/after (all cues, cold and warm); no write call in the handler source; one admit per miss and none per hit; a refused miss is a 429 and calls no provider; authentication first (401/503); only the exact route is exempt; 20 concurrent cues make one provider call; cache unit tests (voice keying, fallback/failure not cached, cancellation, bound).

## Measured (benchmark, baseline 18.7 s sequential / 51.1 s with 4 learners)
Sequential turn p50 **16.8 s** (-1.9 s), p95 19.1 s; cue p50 10.1 s to 2.4 s; coaching gap 0.9 s to 0.0 s; lease retries 44 to 0; lease time mean 1807 ms to 387 ms. Concurrent turn p50 47.1 s.

## Rollback
Unset the flag (about a minute) or revert; the memory cache disappears on restart; no data to clean.
