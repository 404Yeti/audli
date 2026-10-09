# AUD-21 real-microphone acceptance

Opening naturalness acceptance passed: the user confirmed the latest local
real-microphone greeting and reciprocal responses feel natural. Quantitative latency
acceptance remains pending; no representative timestamped dataset was supplied.
Browser RMS/`playing` timings are estimates, not measured acoustic speech-end/output
boundaries. Representative p50/p90 and the ≤5-second target remain unverified.
AUD-21 and AUD-23 remain In Progress.

## Setup

Use a dedicated authorized test learner, real microphone and live existing speech/LLM
providers against an explicitly configured **nonproduction** database/auth environment.
Do not use Playwright, fake media, demo speech, synthetic delays or production learner
data. Confirm provider/model/voice settings and network region; keep them unchanged
between versions. Ordinary test lesson activity writes test progress.

For a local production-mode bundle, with the existing nonproduction environment already
configured, run these in separate terminals from the repository root:

```sh
AUDLI_LATENCY_TELEMETRY=true .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```sh
cd web
NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY=1 npm run build
npm run start -- --port 3000
```

Open `http://127.0.0.1:3000` in Chromium, sign in as the test learner and allow the real
microphone. This is local acceptance, not production acceptance. Runtime-only changes
to `NEXT_PUBLIC_*` do not enable timing in an already-built bundle. Never paste credentials
into DevTools, screenshots, reports or committed files.

Use a quiet room and a fixed input/output device. An observer must hear both the learner
and Audli through the actual output device and operate the markers below. Do not capture
or retain audio, video, transcripts, profile contents, request bodies, tokens or HAR files.
Record browser/OS, device category, network conditions, version SHA, provider/model/voice,
cold/warm classification, UTC test window and measurement method separately, without
personal identifiers. Report observer reaction-time uncertainty; without a calibrated
acoustic reference, these remain human-observed measurements rather than laboratory
precision. Browser events alone cannot establish actual audible onset.

## Manual checklist

- [ ] **Opening:** try a routine status with “How about you?”, then a richer check-in.
  Expect a brief reciprocal reply before the listening transition. Record these as
  separate opening categories; do not pool them with exercise-answer turns.
- [ ] **Short answers:** answer in 1–8 seconds; expect one complete, meaning-based assessment
  and appropriate spoken feedback/follow-up. Repeat across several exercises.
- [ ] **Long answers:** answer in 15–30 seconds; expect the entire answer to be assessed,
  durable progress and no premature coaching.
- [ ] **Mid-answer pause:** pause deliberately for 1.5 seconds, then continue the same
  thought. Audli must keep listening through the pause and assess the continuation.
  Mark speech end only after the final syllable of the whole answer.
- [ ] **Acknowledgment → coaching:** if the optional acknowledgment occurs, expect it
  once, followed by meaningful coaching with no audible overlap, duplicate cue or skipped
  transition. Fast turns may have no acknowledgment: record absent, never zero latency.
  Do not artificially slow a request to obtain a representative acknowledgment sample.
- [ ] **End/cancel:** confirm End during recording, then separately during acknowledgment/
  coaching preparation. Recording/playback must stop; late audio must remain silent.
  Returning after deliberate End must respect the paused checkpoint and require a gesture.
- [ ] **Reload/resume:** reload while assessment/coaching is pending. Restore the saved
  lesson/topic/assessment without duplicate evaluation, premature transcript access or
  duplicate concurrent playback. Unexpected active-lesson refresh may automatically
  restore the saved conversation; deliberate End must stay silent until restart/resume.
  Reinstall the collector after reload. Interrupted timings are separate recovery cases.
- [ ] **Responsiveness:** after each normal turn, rate the perceived gap `prompt`,
  `noticeable`, or `disruptive`; note stutter/overlap/missing audio using fixed categories.
  Confirm personalized, evidence-grounded coaching still fits what was understood.

Also collect welcome/check-in and reflection answers, exercise follow-ups and transitions.
For a transition without new learner speech, measure checkpoint/transition-ready →
first audible playback separately; do not invent a speech-end boundary for it.

## Exact timing collection

1. Open DevTools Console, enable **Verbose** messages, and confirm `[Audli turn]` events.
   Disable log preservation across reloads; export only the numeric collector below.
2. Paste the collector once. It copies only allowlisted stage names and numeric timings;
   it does not read fetch traffic, audio, scripts, credentials or learner identity.
3. Immediately before each spoken answer, call, for example,
   `audliAcceptance.begin('short-answer')`. Then focus a noneditable part of the page.
   The observer presses **E** at the final actual spoken syllable, **A** at the first
   audible acknowledgment word (if present), and **M** at the first audible meaningful
   response word. Do not press E at the deliberate mid-answer pause. These keys only
   collect measurements; they do not stop recording or advance lesson state.
4. After the meaningful response begins, finish the sample with
   `audliAcceptance.finish('success', 'noticeable')`. Use `uncertain`, `retry`, `failure`
   or `cancelled` for other outcomes. Missing markers stay null. Retry/cancelled turns
   must not be mixed into the reliable-turn median; report their counts separately.
5. Inspect `audliAcceptance.samples` and export using Chrome's
   `copy(JSON.stringify(audliAcceptance.samples))`. Keep only this numeric/category data.
   Use `audliAcceptance.stop()` to remove the observer key listener and restore console
   behavior. Reload clears the in-memory collector; export before a planned reload and
   reinstall afterward. Do not attempt to join a recovery sample across clock origins.

```js
(() => {
  window.audliAcceptance?.stop();
  const stages = new Set([
    'silence_detection', 'recording_finalization', 'upload_transcription',
    'assessment', 'checkin_response', 'profile_extraction', 'request_submission', 'response_headers',
    'first_audio_byte', 'audio_body_ready', 'tts_readiness', 'playback_start',
    'acknowledgment_playback', 'turn_to_playback', 'retry_playback',
    'acknowledgment_duration', 'coaching_readiness', 'coaching_wait_after_ack',
  ]);
  const cases = new Set(['short-answer', 'long-answer', 'mid-pause', 'check-in',
    'reflection', 'followup', 'transition', 'cancel', 'recovery']);
  const outcomes = new Set(['success', 'uncertain', 'retry', 'failure', 'cancelled']);
  const ratings = new Set(['prompt', 'noticeable', 'disruptive']);
  const original = console.debug;
  const samples = [];
  let active = null;
  const elapsed = () => Math.round(performance.now() - active.started);
  const debug = function (...args) {
    const value = args[1];
    if (active && args[0] === '[Audli turn]' && stages.has(value?.stage)
        && Number.isFinite(value.elapsedMs)) {
      active.stages.push({stage:value.stage, elapsedMs:value.elapsedMs,
        observedAtMs:elapsed()});
    }
    original.apply(console, args);
  };
  const key = event => {
    if (!active || event.repeat || event.ctrlKey || event.altKey || event.metaKey
        || event.target.closest?.('input,textarea,[contenteditable]')) return;
    const name = {e:'speechEndMs', a:'ackAudibleMs', m:'meaningfulAudibleMs'}[event.key.toLowerCase()];
    if (name && active[name] === null) { event.preventDefault(); active[name] = elapsed(); }
  };
  console.debug = debug;
  document.addEventListener('keydown', key);
  window.audliAcceptance = {
    samples,
    begin(kind) {
      if (!cases.has(kind) || active) throw new Error('Choose a case and finish the previous sample.');
      active = {sample:samples.length + 1, kind, started:performance.now(),
        speechEndMs:null, ackAudibleMs:null, meaningfulAudibleMs:null, stages:[]};
    },
    finish(outcome, responsiveness) {
      if (!active || !outcomes.has(outcome) || !ratings.has(responsiveness))
        throw new Error('Choose a valid outcome and responsiveness.');
      const {started, ...row} = active;
      const delta = end => row.speechEndMs !== null && end !== null
        && end >= row.speechEndMs ? end - row.speechEndMs : null;
      samples.push({...row, outcome, responsiveness,
        speechToAckMs:delta(row.ackAudibleMs),
        speechToMeaningfulMs:delta(row.meaningfulAudibleMs)});
      active = null;
      return samples[samples.length - 1];
    },
    stop() {
      document.removeEventListener('keydown', key);
      if (console.debug === debug) console.debug = original;
      active = null;
    },
  };
})();
```

`acknowledgment_playback` and `turn_to_playback` measure **last detected voice → first
browser `playing`** for acknowledgment and meaningful audio respectively, including
silence detection, finalization, upload and provider processing. They do not measure
actual acoustic boundaries. Compare them with the observer's `speechToAckMs` and
`speechToMeaningfulMs`, never substitute them silently. `first_audio_byte` is delivery,
not sound; `coaching_readiness` is preparation, not playback. `coaching_wait_after_ack`
includes remaining preparation/checkpoint validation and ends before the playback call.

For stage breakdowns, copy only the numeric **Server-Timing** response-header values
for the matching upload, assessment/answer and coaching requests, manually labeled with
sample ordinal and request role (no resource IDs, URL, body or credentials). Observe
request start order in DevTools without exporting network data. Server spans can nest;
do not sum overlapping spans. Match the next coaching readiness to the saved assessment
for that answer, excluding the earlier summary question. Record any ambiguity as missing.

## Representative report

Collect at least **30 reliable evaluated turns per version**, with at least 10 each
short, long and mid-pause answers, across several exercises and multiple time windows.
Collect check-ins/reflections/follow-ups separately and report their own sample sizes.
Repeat live-provider runs; report cold/warm cues, failure/retry counts and variability
between windows. Keep provider settings, difficulty, browser, network and starting test
profile equivalent. Use independent nonproduction baseline/current checkouts and data;
do not reset production learners or overwrite this reviewed checkout.

Report both observer and browser-estimate distributions separately, by turn type and
version. Older baseline revisions may lack separated acknowledgment stages or consume
the turn timer on acknowledgment; mark those browser metrics unavailable and use the
same observer markers for the actual before/after comparison. Compute p50 as the median (average middle pair for even n); p90 as sorted sample
`ceil(0.9*n)-1` (zero-based). Acknowledgment statistics use only turns with acknowledgment
and must state that smaller n; absent acknowledgment is not zero. Transitions, interrupted
turns, untrusted recognition and failed responses are separate outcomes. Include stage
medians/p90 and explicit missing stages, recording duration, measurement uncertainty,
provider variability, correctness failures and responsiveness ratings. No current real
p50/p90 or before/after improvement is claimed.
