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
