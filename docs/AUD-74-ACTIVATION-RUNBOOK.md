# AUD-74 activation runbook (one-person iPhone test)

Status at merge `6a50adc` (20 Oct 2026): flag OFF. Startup log: `Passage TTS {"default_passage_elevenlabs_enabled": false, "daily_characters": 5000, "max_concurrent": 1, ...}`.
Nothing below has been applied. Each step needs explicit owner approval.

## Facts and unknowns
| Item | Value | Source |
|---|---|---|
| Flag | OFF | startup log |
| `AUDLI_PASSAGE_TTS_DAILY_CHARACTERS` | 5000 | startup log |
| `AUDLI_PASSAGE_TTS_MAX_CONCURRENT` | 1 | startup log |
| `accent_control` `daily_limit`/`concurrent_limit`/`characters`/`enabled` | **unknown** (AUD-38 recorded 5000) | needs owner read |
| ElevenLabs Creator credits | **unknown** (29,237 left on 10 Oct) | needs owner read |

Effective ceiling = `min(accent_control.daily_limit, 5000)`; effective concurrency = `min(accent_control.concurrent_limit, 1)`.
The budget is spent on reservation (full script length, no refund) and is shared with accent practice.

## Tooling access (why the unknowns are unknown)
- Render MCP: logs, deploys, services only. No env-var read tool. `query_render_postgres` lists no Postgres instance (production uses Supabase).
- No Supabase/SQL connection or database URL is available to the agent.
- ElevenLabs MCP: agent/creative generation tools only; none returns subscription or credit balance (generation tools would spend credits). The service key lives only in Render.
- Needed from the owner: (a) Supabase SQL editor (read), (b) ElevenLabs dashboard (Usage), or a read-only subscription call run by the owner.

## Preflight (owner, read-only)
1. `select enabled, daily_limit, concurrent_limit, budget_day, characters from accent_control;`
   Need `daily_limit >= 5000`, `concurrent_limit >= 1`, and `characters` today (UTC `budget_day`) leaving >= 2,000 unused. If `budget_day` is an earlier day, usage is 0.
2. ElevenLabs dashboard: remaining credits >= 30,000 (test itself costs ~0.5k).
3. Render `audli-api` env: confirm `ELEVENLABS_API_KEY` present (log shows `elevenlabs_key_configured: true`).
4. Quiet window: the flag is global, not per learner. Any learner generating a passage while it is on gets the new route. Check recent `Passage routing` log lines for other users first.

## Activation (one change)
Set `AUDLI_DEFAULT_PASSAGE_ELEVENLABS_ENABLED=true` on Render `audli-api`; let it redeploy. No budget, concurrency, or control-row change is needed for a one-person test.
Expected startup line: `Passage TTS {"default_passage_elevenlabs_enabled": true, "voice_id": "yKYzqEa22xh5PdidhN70", ...}`.
Startup refuses to boot if the key is missing or the daily ceiling is 0, so a bad config keeps the previous deploy live.

## Test (one tester, real iPhone)
Budget: a passage is ~500-1,000 characters, so two passages use ~1,000-2,000 of the 5,000.
1. Start a lesson; let one passage play. Listen: Audli voice, not Coral; one clear speaker.
2. Look for exactly one `Passage TTS {"event": "default_passage", "outcome": "success", ...}` line with that voice ID, and `Passage routing provider=elevenlabs`.
3. Optionally a second lesson/passage. After any failure wait **90 s** before retrying (the slot stays leased; concurrency is 1 and a retry earlier shows `concurrency_limit`).
4. Pass criteria: success lines only, no `fallback_reason`, `characters` consumed matches script length, tutor cues unaffected (`Tutor TTS` still ElevenLabs), no new errors, exercise replays correctly (stored audio).
5. Stop on any `outcome=fallback` (note the reason), 5xx, or Coral audio.

## Rollback
1. Set the flag to `false` (or delete it); redeploy. Verify the startup line shows `default_passage_elevenlabs_enabled: false`.
2. Already-created exercises keep their stored audio; no data change; reservations expire within 90 s.
3. Budget and control row were not changed, so nothing else to revert. Characters spent today stay spent.

## Scale-up (separate approval)
Raise `AUDLI_PASSAGE_TTS_MAX_CONCURRENT` to 2-3 and both daily ceilings (env + `accent_control.daily_limit`) to ~10,000 after the owner reviews a day of logs and the credit delta.

## Status log
- 2026-10-20 22:37 UTC: global activation (owner-approved product decision: ElevenLabs is the default passage provider for all users). Render deploy `dep-dbbuq2jl9sls739b4j7g`, revision `6a50adc`, flag `true`, `daily_characters=5000`, `max_concurrent=1`, voice `yKYzqEa22xh5PdidhN70`. Budget, concurrency, `accent_control`, tutor and accent settings unchanged.
- Rollback trigger conditions: sustained passage failures, unexpectedly high credit use, degraded tutor speech, or lesson-reliability regression. Action: set the flag `false`, redeploy, confirm the startup line shows `default_passage_elevenlabs_enabled: false`.
- Alert thresholds (owner): remaining credits < 5,000, or frequent passage fallbacks.

## Follow-up (not implemented; needs separate approval): overlapping passages should not fall back to Coral unnecessarily
Problem. With `AUDLI_PASSAGE_TTS_MAX_CONCURRENT=1` the shared ledger admits one passage synthesis at a time; a second learner starting a passage while one is in flight (or within the 90 s lease after a failed attempt) gets `fallback_reason=concurrency_limit` and hears Coral, although ElevenLabs Creator Flash allows 10 concurrent requests account-wide.

Options, smallest first:
1. **Raise the cap to 2-3** (`AUDLI_PASSAGE_TTS_MAX_CONCURRENT` and `accent_control.concurrent_limit`). Configuration only; leaves >= 7 of 10 slots to the tutor. Watch the `inflight_at_start` field.
2. **Bounded wait before fallback.** In `default_passage_audio`, when admission returns `concurrency_limit`, retry `reserve_default` for up to a few seconds (e.g. 3 x 1 s) before falling back. A normal passage holds the slot ~5 s and the learner already waits ~7-10 s for generation, so most overlaps resolve with no Coral. Pure code, tested with the existing burst test.
3. **Release failed slots sooner.** A failed or timed-out attempt currently holds its slot for the 90 s lease. A definitive provider answer (HTTP error, invalid audio) could release immediately; only an ambiguous timeout needs the lease.
4. **Pre-generate the next passage** while the learner answers, removing generation latency from the critical path (larger change; interacts with the lesson lifecycle, so out of scope here).
5. **Circuit breaker for quota exhaustion.** After `quota_exceeded` skip ElevenLabs for passages (and tutor cues) for a short period instead of paying one failed attempt per request.

Recommended order: 1 (config) then 2 and 3 (small code) after at least a week of fallback-reason data.

## Credit-burn caveat (observed 20 Oct 2026)
Creator balance read by the owner: 16,791 of 121,314 remaining, renewing 3 Nov. Usage 92,077 (10 Oct) -> 104,523 (20 Oct) is ~1.2k credits/day before this change. The 5,000-character budget adds up to ~1.1k credits/day, so the balance can reach the 5,000 alert floor in roughly 5-7 days. Monitor daily; raise credits or lower the budget before exhaustion (exhaustion degrades to Coral, never to silence).
