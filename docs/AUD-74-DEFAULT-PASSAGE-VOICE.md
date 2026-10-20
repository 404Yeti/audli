# AUD-74: ordinary listening passages in the Audli ElevenLabs voice

## Finding
Ordinary (non-accent) passages were synthesized by OpenAI with `AUDLI_VOICE` (default `coral`) by design.
ElevenLabs was used only for consented accent passages (AUD-20). The tutor voice (AUD-25) never covered passages.

## Change
`AUDLI_DEFAULT_PASSAGE_ELEVENLABS_ENABLED` (default `false`). When `true`, ordinary passages use the deployment's
configured tutor voice ID (`AUDLI_ELEVENLABS_VOICE_ID`, no second variable) with the tutor's stability, similarity,
style and speaker-boost settings plus the lesson speed. Accent routing and consent are unchanged; when an accent
passage cannot be produced, the "usual passage voice" is now this voice (still reported `fallback=true`).

Flag OFF is byte-for-byte the previous route. Startup validation rejects the flag without `ELEVENLABS_API_KEY`,
with `AUDLI_PROVIDER=demo`, or without a positive `AUDLI_PASSAGE_TTS_DAILY_CHARACTERS`.

## Safeguards (reused, not new knobs)
- Daily characters and concurrency: `reserve_default` draws on the same durable fleet ledger as accents
  (`min(control daily_limit, AUDLI_PASSAGE_TTS_DAILY_CHARACTERS)`, `min(control concurrent_limit, AUDLI_PASSAGE_TTS_MAX_CONCURRENT)`),
  independent of the accent kill switch. Characters are never refunded. Raise the fleet `daily_limit` before enabling
  or default passages will exhaust the budget and fall back.
- Tutor capacity: with the default `AUDLI_PASSAGE_TTS_MAX_CONCURRENT=2`, passage synthesis can hold at most two of the
  plan's concurrent ElevenLabs slots (Creator/Flash: 10). The tutor's inflight gauge counts passage requests too.
- Timeout `AUDLI_PASSAGE_TTS_TIMEOUT_SECONDS` (20 s), one attempt, no retry, max `AUDLI_PASSAGE_TTS_MAX_CHARACTERS`.

## Fallback (explicit, not silent)
OpenAI/Coral is retained only for availability. Every fallback logs one WARNING `Passage TTS {...}` with
`outcome=fallback`, `fallback_provider=openai` and a fixed `fallback_reason`: `unsupported_rate`, `missing_credentials`,
`admission_unavailable`, `control_unavailable`, `too_long`, `budget_exhausted`, `concurrency_limit`, `timeout`,
`network`, `invalid_audio`, `http_rejected`, or a provider status (`quota_exceeded`, `invalid_api_key`, ...).
Success logs provider, voice ID, model, characters, inflight, cost units and latency. No script, learner ID, URL,
exception text or key is logged. A startup `configuration` event records whether the route is enabled.
The stored `PassageVoice` records the voice that actually spoke (`elevenlabs` + voice ID, or `openai` + Coral).

## Existing audio and sessions
Audio is stored per exercise and `prepare_exercise` reuses an unfinished exercise, so nothing is regenerated and no
session is invalidated. Tutor synthesis, tutor cache signatures and the lesson lifecycle are not touched.

## Tutor vs passage TTS differences (voice-ID reuse)
| | Tutor | Passage |
|---|---|---|
| Endpoint | `/stream` | non-stream (full body) |
| Model | `AUDLI_ELEVENLABS_MODEL` (flash or multilingual_v2) | `AUDLI_PASSAGE_TTS_MODEL` (flash only) |
| Voice settings | stability, similarity, style, boost, speed | accent route: speed only; default route: matches tutor |
| Timeout | 15 s, then OpenAI 20 s | 20 s |
| Speed range | .7–1.2 | .7–1.2 |
| Voice ID | per-user (`original`/`male` preference) | deployment's `AUDLI_ELEVENLABS_VOICE_ID` only |
| Audio cap | 2 MB | 8 MB |

Consequences: the male-tutor preference does not change the passage speaker (one clear speaker per passage;
changing this needs a product decision). If the tutor model is set to `multilingual_v2`, passages stay on flash.

## Rollout
Not enabled. Before enabling: raise the fleet daily limit, confirm `ELEVENLABS_API_KEY`, set the flag, watch
`Passage TTS` fallback reasons and the inflight gauge, listen on a real iPhone. Rollback: unset the flag.
