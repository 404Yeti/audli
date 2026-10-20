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

**Decision (owner, 20 Oct 2026): the initial release intentionally uses Audli's canonical ElevenLabs voice (`AUDLI_ELEVENLABS_VOICE_ID`) for every ordinary passage, independent of the learner's male tutor voice preference.** Passages are one stable narrator; the preference only changes the tutor.

Consequences: the male-tutor preference does not change the passage speaker (one clear speaker per passage;
changing this needs a product decision). If the tutor model is set to `multilingual_v2`, passages stay on flash.

## Rollout
Not enabled. Before enabling: raise the fleet daily limit, confirm `ELEVENLABS_API_KEY`, set the flag, watch
`Passage TTS` fallback reasons and the inflight gauge, listen on a real iPhone. Rollback: unset the flag.

## Capacity review (read-only; no production setting changed)
Sources and confidence: [M] measured in production logs, [D] documented earlier, [E] estimate.

**Passage size.** The generator targets `words = duration_seconds * 150 * speech_rate / 60`, within ±10% [code]. At ~6 characters per word [E]:
bounds 30 s x 0.6 = 45 words (~270 chars) to 90 s x 1.15 = 259 words (~1,550 chars); default 45 s x 0.75 ~ 500 chars; typical 60 s x 0.9 ~ 800 chars.

**Credit rate.** ElevenLabs `character-cost` on production Flash tutor calls is ~0.22 credits per character [M, 14 samples: 83 chars -> 18, 372 -> 82, 368 -> 81].
Treat as an estimate until confirmed on the account.

**Volume [M].** Production routing logs 13-20 Oct show ~16 ordinary passages in the retained window: 10 on 17 Oct (testing), 3 on 18 Oct, 3 on 20 Oct. Early beta.

| Scenario | Passages/day | Chars/day (800 each) | Credits/day | Credits/30 days |
|---|---|---|---|---|
| Observed typical | 3-4 | ~3,000 | ~700 | ~21k |
| Observed peak | 10 | ~8,000 | ~1,800 | ~53k |
| Current documented ceiling 5,000 | ~6 | 5,000 | 1,100 | 33k |
| Pilot recommendation 10,000 | ~12 | 10,000 | 2,200 | 66k (hard worst case) |

Accent practice draws from the same pool (reservation = full script length, never refunded, including failures). Tutor speech is separate and
unmetered by Audli: ~20 cues/lesson x ~90 chars ~ 1,800 chars ~ 400 credits per lesson [E].

**Account quota [D, stale].** Creator plan, 121,314 credits, 92,077 used and 29,237 remaining on 10 Oct 2026 after the AUD-25 evaluation spend; overage flags were false.
This was not re-read now (no credential used). Verify the live balance before enabling: ordinary passages roughly double ElevenLabs credits per lesson.

**Application limit [D, not re-read].** AUD-38 records `daily_limit=5000` in `accent_control` and `AUDLI_PASSAGE_TTS_DAILY_CHARACTERS=5000`. The effective ceiling is the **minimum** of the two,
so both must be raised. Live values: `select enabled, daily_limit, concurrent_limit, budget_day, characters from accent_control;` and the Render env var.

**Recommendation.** Initial pilot ceiling **10,000 characters/day** in both places (about 12 typical passages: covers the observed 8,000 peak plus accent headroom; worst case 2,200 credits/day).
At 5,000 roughly half of observed peak-day passages would fall back to Coral, producing a mixed-voice pilot. Do not enable unless the live Creator balance covers
at least ~30 days of observed typical use (~21k credits) plus tutor use; review after 3 days of `Passage TTS` logs and raise or lower.

**When a limit is exhausted.**
- Application budget (or concurrency): `reserve_default` refuses (`budget_exhausted` / `concurrency_limit`); no ElevenLabs call is made; that passage uses OpenAI/Coral and a WARNING is logged. Accent requests lose the same pool and also end on the usual voice path (then Coral).
- ElevenLabs account quota (401/402/429 `quota_exceeded`): the single attempt fails, reason `quota_exceeded` (or `http_rejected`), the passage uses Coral, the budget is not refunded. There is no circuit breaker: every passage and tutor cue keeps paying one failed attempt of latency until quota returns.

**Tutor starvation.** ElevenLabs Creator Flash concurrency is 10 account-wide [D, AUD-70]. Passage synthesis (default and accent together) is admitted through one
durable ledger capped at `min(concurrent_limit, AUDLI_PASSAGE_TTS_MAX_CONCURRENT=2)`, and a failed or timed-out slot stays leased for 90 s. So passages can hold at most
2 of 10 slots; the remaining 8 are never reserved by passages. The tutor has no limiter, so a burst of tutor requests can still push a *passage* over the account limit; that passage then falls back to Coral (never the reverse).
Tests: `test_burst_never_exceeds_passage_concurrency_cap` (6 concurrent requests -> peak 2, 4 explicit fallbacks), `test_reserve_default_is_atomic_across_threads_and_shared_with_accents`.

**Startup flag and rollback.** Unset/`false` = previous route. `true` requires key, non-demo provider and a positive daily ceiling or the process refuses to start (so a bad deploy fails health checks and Render keeps the previous deploy).
A `Passage TTS {"event": "configuration", ...}` line states whether the route is enabled. Rollback: unset the variable (or set `false`) and redeploy/restart; existing exercises keep their stored audio, in-flight leases expire within 90 s.
