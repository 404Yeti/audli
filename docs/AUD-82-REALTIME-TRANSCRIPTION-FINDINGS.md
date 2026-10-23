# AUD-82 — Realtime transcription experiment: findings (synthetic phase)

Status: harness built and run on **synthetic audio only**. No production change, no learner data, nothing connected to scoring or progression. Estimated spend **$0.66 of the $5.00 cap** (ledger; $2.50 reserved for the consented pilot is untouched). **No production integration is proposed here.**

## What was and was not tested
* 46 speech clips (38 prompts: read-aloud, learner-style poor grammar, short answers, numbers; 12 scripted pause / hesitation / self-correction / short clips) plus 4 non-speech clips, spoken by TTS told to sound Vietnamese. **That is not evidence about real Vietnamese-accented learners.** It validates plumbing, latency, endpointing mechanics and safeguard mechanics only.
* Arms: A0 current production path (client detector, upload, `gpt-4o-mini-transcribe`, logprob confidence gate); A1 `gpt-live-transcribe` (delay minimal / low / high, endpoint window 1800 / 1200 ms / pause-aware); A2 `gpt-transcribe` committed turns.
* Replayed in real time from this machine straight to OpenAI. A0 therefore **excludes** the browser to Vercel to Render to Supabase overhead measured separately in PR #81, so it is a best case for the REST path.
* Not tested: iPhone Safari (no device; WebKit cannot run here), real accented or noisy speech, concurrency, long sessions.

## Verified API capabilities (probe, account-specific)
`gpt-live-transcribe`, `gpt-transcribe`, `gpt-4o-mini-transcribe` all available. Session fields as documented are accepted, all five `delay` values (minimal, low, medium, high, xhigh), prompt / keywords / languages hints; an invalid `delay` is rejected with `invalid_value`. Short-lived client secrets mint with HTTP 200 and a WebSocket authenticated through the browser-style subprotocol is accepted (from a script, and from real Chromium; Safari untested). Streamed events seen: `session.created/updated`, `input_audio_buffer.committed`, `conversation.item.added/done`, transcription `delta` and `completed`. **No confidence or logprob field was observed.** Prices: `gpt-live-transcribe` $0.017/min and `gpt-transcribe` $0.0045/min (model pages); `gpt-4o-mini-transcribe` is token-priced (charged at a high $0.006/min assumption).

## Latency (end of last speech to final transcript; p50 / p90, n=46)
| Arm | Window | p50 | p90 | After commit | First partial after speech start |
|---|---|---|---|---|---|
| A0 REST | 1800 | 2.77 s | 3.93 s | REST request 0.97 s | none |
| A0 REST | 1200 | 2.13 s | 4.43 s | 0.97 s | none |
| A1 live, delay low | 1800 | **2.41 s** | **2.67 s** | 0.62 s | 0.95 s |
| A1 live, delay minimal | 1800 | 2.49 s | 2.60 s | 0.70 s | **0.54 s** |
| A1 live, delay high | 1800 | 2.51 s | 2.63 s | 0.73 s | 1.77 s |
| A1 live, delay low | 1200 | **1.81 s** | 2.04 s | 0.62 s | 0.90 s |
| A1 live, low, pause-aware | 1200+ | 1.83 s | 2.01 s | 0.69 s | 0.90 s |
| A2 `gpt-transcribe` | 1800 | 2.53 s | 2.72 s | 0.73 s | 5.7 s (no partials before commit) |

* Same endpoint window: streaming beats REST by about **0.36 s at p50 and 1.3 s at p90** (mostly a tighter tail). Final text still takes ~0.6-0.7 s after the commit; `delay` changes how early partial text appears, not that wait.
* Most of the gain in the table is the **endpoint window**, not streaming: moving 1800 to 1200 ms saves ~0.6 s for either path (REST 2.77 to 2.13 s).
* `gpt-transcribe` sends nothing before the commit and is no faster than REST by more than ~0.2 s.
* **Dual-path consequence:** with the REST text kept authoritative (required by the trust model), the critical path is still the REST call. Streaming helps only if the assessment is started speculatively from the streamed text while REST verifies in parallel: a saving of roughly the REST-minus-streaming gap (~0.3-0.4 s median), at an extra $0.017/min.

## Accuracy
On clean synthetic speech all arms are close and the corpus cannot separate them. Uncut, non-numeric clips (n=36): mean WER A0 3.8%, A1 low 3.4%, A1 high 2.8%, **A1 minimal 6.1% (errors on 10 clips)**, A2 0.7%; differences rest on 3-4 clips each. Notes: REST writes digits and symbols ("9:15", "$50", "13th"), streaming spells numbers out, so scoring or evidence matching must normalize numerals. Fillers are dropped by every arm. Both self-correction clips kept the corrected word in all arms ("Thursday, no, I mean Friday" retained "Friday"). One very short answer scored WER 1.0 on REST, 0.5 on A1, 0 on A2 (noise at this size). No arm produced text for the 4 non-speech clips.

## Endpointing, pauses and the cut-off hazard
* TTS adds silence at each segment edge, so "1.6 s" pauses measured 2.6 s. The set therefore has gaps of at most 1.15 s (never cut) or 1.95-3.25 s: **6 of 12 hesitation clips were cut off under every window tested (1800, 1200, pause-aware)**, and none in the 1.2-1.9 s range where the windows differ. This set cannot say which window is safe for ESL speakers; real pause data can.
* A pause-aware window learns only after hearing a first pause shorter than the base window, so it did not help here.
* **No transcription safeguard detects a cut-off.** The truncated answers keep 3-5 of 8-12 words and the transcript is faithful to the audio captured; the production confidence gate and the agreement check accepted 5 of the 6. The backstop is the existing evidence flow (insufficient evidence leads to a follow-up question). A streaming design could keep the turn open and append more audio after a commit; not tested.

## Confidence safeguards (the streaming models give no confidence)
On the 36 uncut non-numeric clips (STT errors only):
| Safeguard | Accepted | False-reliable (accepted, WER > 0.25) | False-unreliable (rejected, WER <= 0.10) |
|---|---|---|---|
| Production logprob gate (REST) | 29 | 0 | 6 of 7 rejections |
| S1 streaming-vs-REST agreement <= 0.2 | 29 | 0 | 5 |
| S2 `gpt-transcribe` `languages` non-empty | 36 | 0 | 0 (it accepted everything) |
Six of the seven production-gate rejections are 1-2 word answers that were exactly right ("too little recognized speech"), a known issue (AUD-68). **Zero false-reliable on 36 clean clips validates nothing**: the criterion needs at least 100 human-checked, accented clips. S1 mechanically works but only exists with the REST call (the dual path); S2 never fired on clean speech and is unproven. There is **no validated replacement** for the logprob gate yet.

## Cost
Streaming $0.017/min vs REST about $0.003-0.006/min. With an assumed 4 minutes of open microphone per lesson: streaming about **$0.07 per lesson**, REST about $0.01-0.02; the dual path pays both (about +$0.07 per lesson, about $70 per 1,000 lessons). Streaming for the whole 10 minutes would be $0.17. The 4-minute figure is an assumption pending measured data.

## Safari / iPhone
Not tested on a device. The test page ran 3 consecutive turns on one socket in headless Chromium with a fake microphone (44.1 kHz hardware rate resampled to 24 kHz, browser-style secret auth). `browser/SAFARI.md` lists the iOS risks (continuous microphone, play-and-record audio route, interruptions, socket loss on lock or network change) and pass criteria for a real run.

## Criteria status (from the proposal)
| Criterion | Status |
|---|---|
| Streaming at least 1.0 s faster at p50 than REST | **Not met** at equal endpointing (~0.36 s); met only by the shorter window, which also helps REST |
| WER within 2 points of baseline | Met on synthetic speech, not validated on accented speech |
| Self-correction wording kept in 90% | Met (2 of 2); sample too small |
| False cut-offs at most 2% with pauses up to 2 s | **Not met by any window** (6 of 12 hesitation clips); synthetic gaps |
| Confidence replacement with false-reliable at most 2% on 100+ clips | **Not validated** |
| iPhone Safari 20 turns x 3 runs | **Not run** |
| Cost per lesson at most $0.25 | Met on the assumption (~$0.07) |

## What this suggests (no production integration proposed)
1. On this evidence streaming transcription is not the biggest lever. The same data shows a **0.6 s saving from the endpoint window** for either path, and PR #81 is measuring a 1-3 s per-request backend overhead, plus the ~2.8 s assessment call.
2. The decision that needs real data is the **pause distribution**: log the longest internal pause per turn (numbers only) in telemetry to choose a window; this is free.
3. If streaming is still wanted, the next step is the consented pilot (5 speakers, about $1 of the remaining $2.50 reserve) for accent accuracy and the safeguard rates, plus the iPhone run. Without those, S1/S2 stay unvalidated and the browser transcript must stay non-authoritative.
