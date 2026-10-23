# AUD-82 — Pause-duration telemetry (measurement only)

Why: the realtime-transcription experiment showed that no endpoint window up to 1.8 s survives a 2 s pause, and that synthetic pauses cannot tell us which window is safe for real ESL speakers. Real pause data is the free way to decide. **This PR changes no threshold and no learner-facing behaviour**: the end-of-turn rules in `turnDetector` are untouched.

## What is measured
Per spoken turn, once, when the turn ends (browser-side, only when `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1`):
* `longest_pause`: the longest silent gap **inside** the answer, between voiced samples, counted only after 300 ms of voice has been heard (leading silence, brief noise blips and the final silence that ends the turn never count). Reported in ms.
* `speech_span`: first to last voiced sample, in ms.
Turns with under 300 ms of voice report nothing.

## What the server stores
Two bounded distributions in the existing `Latency summary` log lines, under `gauges`:
* `client.longest_pause_100ms`: level n means the longest pause was n x 100 ms (so `12` is 1.2 to 1.3 s); capped at 32 (3.2 s and longer).
* `client.speech_span_s`: whole seconds, capped at 32.
Counts per level only. No identifiers, no timestamps per turn, no text, no audio, and nothing links a value to a learner or a lesson. The request schema is a fixed enum of stage names with integer values 0 to 300000, authenticated like every `/api` route.

## How to read it
With `n` turns reported: the share of turns whose longest pause is at least 1.2 s is `sum(levels >= 12) / n`; at least 1.8 s is `sum(levels >= 18) / n`. A window W cuts off about that share of turns (an upper bound: the app's follow-up flow catches some). Report only when n is at least 100 (see the telemetry rollout checklist); compare by speaker group only with consent. The 1.8 s wait itself is a constant, so the saving from a shorter window is `1.8 s - W` per turn.

## Not changed
`turnDetector` decisions, the 1.8 s silence window, recording, upload, transcription, scoring, adaptation. Direct-API and fast-sanitization flags remain off.
