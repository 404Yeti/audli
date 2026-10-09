# AUD-21: TTS first-audio feasibility (9 October 2026)

## Publication, availability and deferred work

AUD-21 latency optimization is paused in favor of onboarding and beta readiness.
The proposed sanitizer experiment and streaming gates below are **deferred**;
no further latency optimization is authorized by this report. This branch adds
only documentation. See the [rejected compact-evidence report](AUD-21-COMPACT-EVIDENCE-AB.md)
and the [application flow](APPLICATION_FLOW.md) for related context.

The scripts `scripts/benchmark_tts_feasibility.py`,
`scripts/benchmark_tts_browser.mjs`, and `scripts/analyze_tts_feasibility.py`
remain unpublished local research artifacts in the original Audli workspace.
**They are not included in this PR or available from a fresh clone of this
branch.** No separate published script archive currently exists.

The ignored `data/tts-feasibility-20261009-live/` dataset is local only. It contains
the manifest, 90 numeric provider samples, 180 browser probes, joined analysis,
180 synthetic sanitized full/prefix MP3 clips, summaries and `SHA256SUMS.json`.
All 186 covered artifact hashes were verified before publication. The earlier
`data/tts-feasibility-20261009/` directory contains only the failed sandbox run's
manifest; it supplies no measurements. Neither directory is uploaded in this PR.

Reproduction commands below apply only after obtaining the unpublished scripts
and synthetic archive from the workspace owner and verifying its manifest.
Install the repository's existing development dependencies and matching
Playwright browser, privately configure the existing provider credentials, and
use a new output folder. Requests incur provider charges. A fresh checkout alone
cannot reproduce this study. No learner material, credentials or audio binaries
are included in these reports.

## Decision and scope

The existing evaluator remains authoritative. The rejected compact experiment
was already isolated: no tracked implementation diff, no production imports,
and explicit development-only CLI guards. Retain its scripts, fixtures, tests,
report and ignored synthetic artifacts for reproducibility. Revalidation of 600
rows reproduces 176.699 ms median improvement, below 500 ms, with the contradiction
and coaching regressions documented in `AUD-21-COMPACT-EVIDENCE-AB.md`. No cleanup
requires overwriting or deleting application code. No unrelated changes were found.

This study adds offline scripts and documentation only. Models, voice, speed,
production audio handling, acknowledgment, evaluator, authentication, transcript
gates, state transitions and the 1.8-second silence threshold are unchanged.
Branch: `feat/beta-feedback-forms`; HEAD: `2467176`. Nothing committed, pushed,
merged or deployed. AUD-21 stays In Progress; AUD-23 was not modified.

## Current critical path

`app/services/openai_provider.py:OpenAIProvider.speak` calls speech.create and
returns the complete buffered MP3. It uses `gpt-4o-mini-tts`, configured voice
`coral`, speed 0.9 for coaching, and the instruction “One clear English speaker.
Friendly, clear delivery without background sounds.” The SDK has a 90-second
timeout and two retries. The benchmark keeps these values.

Exercise assessment and deterministic adaptation are validated and persisted in
`app/conversation_api.py` before feedback text is available. The coach-audio route
checks ownership, cue gate and lesson/revision, then reads the versioned feedback
cache or synthesizes, sanitizes, checks the checkpoint again and persists complete
audio. Failed cache persistence removes the temporary audio file. `Accept:
audio/*` already returns bytes directly, avoiding a second authenticated fetch.
TTS failure leaves the saved assessment intact. Lesson speech and acknowledgments
also use complete-message caches scoped to verified learner identity.

`app/audio.py:sanitize_generated_audio` writes a temporary source, invokes ffmpeg
to decode/re-encode with metadata removed and a 150-second limit, waits for a
successful exit under a 30-second timeout, and reads the resulting file. It is
the validation/sanitization boundary: there is no early learner playback here.

The browser (`web/lib/voice-lifecycle.ts`, `prepared-audio.ts`, `api.ts`) authenticates
fetches and consumes the full body before creating a Blob URL. Preparation overlaps
acknowledgment; playback remains serial. Account revision, operation cancellation,
lesson lifetime and delayed-checkpoint checks discard stale work. Cancellation
pauses audio and revokes URLs. Interrupted or failed audio returns to retry/recovery
without undoing assessment. The current server can finish a provider request after
browser cancellation; browser abort alone does not prove provider cancellation.

## Provider documentation

[Official OpenAI speech documentation](https://developers.openai.com/api/docs/guides/text-to-speech)
documents incremental speech delivery and the Python
`audio.speech.with_streaming_response.create` interface. It supports MP3 and other
formats. Its WAV/PCM latency recommendation is not tested or adopted here: keeping
the current format makes this investigation directly relevant to the existing path.
Transport chunks are byte boundaries, not promised MP3 frames, sentences or
independently playable files. The guide's examples do not establish Audli's
sanitization, authentication, cache or recovery safety.

## Method and measurement boundaries

Three fixed synthetic texts: the existing four-word acknowledgment, 27-word
coaching and 47-word coaching. No real learner material is used. See the immutable
run manifest for exact texts. Thirty requests per workload, 90 total, serial
round-robin with rotating workload order; no API, database, browser session or
production cache is opened. All provider timings use one monotonic clock.

The HTTP client captures SDK retry headers numerically. Chunk iteration has no
configured buffer size, preserving the first nonempty SDK body delivery. It still
measures user-space delivery, not the network's first TCP byte. Response headers
are measured separately. Full-body completion includes all delivery; it is an
equivalent buffered-payload baseline from the same stream, not a separately
randomized comparison against speech.create. Connection warmup and outliers are
retained. There is no claim of production traffic representativeness or of
unchanged service timing across hosts/dates. Provider billing is not measured.

After each complete capture, every prefix at consecutive observed chunk boundaries
is decoded with ffmpeg until a usable segment is found. First decodable means
successful exit plus nonempty PCM; this can be only milliseconds of sound. First
independently playable candidate means at least 0.5 seconds of decoded audio,
passed through the **unchanged** full-file sanitizer, verified as MP3 and decoded
again. It is then played as an independent Blob in Chromium. The prefix includes
all preceding bytes; an arbitrary middle chunk was not tested independently.

Prefix analysis is post-hoc. Arrival timestamps locate when bytes existed; decode,
sanitization and verification costs are measured later. Their sums are projected
readiness, not observed live streaming readiness. Repeated trial decoding is
excluded from that projection; a real implementation would need a bounded parser
and validation strategy. Sampling chunk boundaries bounds the earliest observed
candidate; it does not discover a mathematical first decodable byte. A playable
half-second need not contain a complete phrase or meaningful feedback. No speech
recognition or human naturalness certification of prefixes is claimed.

Chromium probes use preloaded sanitized bytes, fresh Blob URLs and an Audio element
after a user gesture. Startup is src assignment to first `playing`, excluding
Node-to-browser transfer, authentication, network/proxy, persistence and actual
acoustic onset. Headless WSL results cannot substitute for a real-device microphone
benchmark. Prefix playback does not demonstrate concatenation, gap-free continuity
or cross-browser support.

## Results

90/90 valid provider samples; 30 per workload, zero failures, zero SDK retries.
180/180 independent full/prefix browser probes passed on headless Chromium
153.0.8010.12. p50 is the median, p90 nearest rank. All values below are seconds,
shown as p50 / p90. These are TTS-only measurements, not microphone turn targets.

| Measurement | Acknowledgment | Short coaching | Long coaching |
|---|---:|---:|---:|
| Response headers | 0.885 / 1.314 | 1.029 / 1.277 | 1.011 / 1.183 |
| First raw body bytes | 0.885 / 1.314 | 1.029 / 1.277 | 1.011 / 1.183 |
| First decodable prefix arrival, confirmed post-hoc | 0.885 / 1.314 | 1.029 / 1.277 | 1.011 / 1.208 |
| First decode processing cost, separately measured | 0.176 / 0.192 | 0.179 / 0.190 | 0.176 / 0.192 |
| >=0.5-second playable prefix arrival, confirmed post-hoc | 1.169 / 1.932 | 1.176 / 2.398 | 1.162 / 1.853 |
| Complete provider audio | 1.363 / 2.167 | 2.149 / 4.747 | 3.087 / 4.398 |
| Full-file sanitizer, including its decode validation | 0.227 / 0.249 | 0.308 / 0.328 | 0.392 / 0.414 |
| Extra full-file decode verification, study only | 0.184 / 0.202 | 0.201 / 0.212 | 0.220 / 0.243 |
| Prefix sanitizer | 0.205 / 0.219 | 0.207 / 0.221 | 0.205 / 0.224 |
| Extra prefix decode verification | 0.181 / 0.196 | 0.179 / 0.197 | 0.181 / 0.192 |
| Full Blob startup to playing | 0.0067 / 0.0079 | 0.0074 / 0.0084 | 0.0076 / 0.0088 |
| Prefix Blob startup to playing | 0.0068 / 0.0072 | 0.0069 / 0.0078 | 0.0069 / 0.0075 |

First raw bytes and first decodable prefixes have nearly identical aggregate
quantiles, but are not interchangeable per sample: 12/90 first chunks failed the
decode probe. Prefix availability is earlier
than validated readiness. A successful short prefix also cannot certify the
remaining response: corruption, stalls or disconnect can still occur later.

| Within-sample estimate | Acknowledgment | Short coaching | Long coaching |
|---|---:|---:|---:|
| Complete minus playable-prefix arrival | 0.176 / 0.224 | 0.952 / 2.349 | 1.950 / 2.318 |
| Projected complete sanitized Blob playing | 1.604 / 2.398 | 2.463 / 5.067 | 3.499 / 4.795 |
| Projected prefix sanitized/verified Blob playing | 1.579 / 2.308 | 1.582 / 2.792 | 1.568 / 2.278 |
| Paired projected saving | 0.012 / 0.077 | 0.874 / 2.275 | 1.957 / 2.303 |

The projected full path is complete arrival + current sanitizer + measured full
Blob startup. The prefix projection is its arrival + current sanitizer + extra
decode verification + prefix Blob startup. These sums deliberately exclude the
study's extra full-file verification from the production baseline. They do not
include trial prefix decoding, proxy/network delivery, revision checks, queueing,
cache persistence, acknowledgment remaining time or sustained playback buffering.
The median of paired savings differs from subtracting separate medians.

Acknowledgments gain almost nothing after validation costs and are already cached;
do not change them. The longer coaching result provides enough technical headroom
to justify a separate, gated investigation, but **does not justify production
streaming yet**. Neither complete meaningful phrase onset nor continuous sanitized
playback was measured. Safe production savings may be smaller, particularly while
acknowledgment is still playing. A 0.5-second segment can end mid-word and carries
no guarantee of conversational usefulness. Thirteen of thirty acknowledgments
had negative projected savings after verification costs.

| Complete-provider variability | SD (s) | Minimum (s) | Maximum (s) |
|---|---:|---:|---:|
| Acknowledgment | 2.355 | 0.812 | 14.318 |
| Short coaching | 1.767 | 1.642 | 10.512 |
| Long coaching | 0.707 | 2.583 | 5.761 |

The first acknowledgment took 14.011 seconds to headers/body and 14.318 seconds
to completion, with no retry. It is retained; initial connection/provider delay
was not attributed. It is unrelated evidence to the historic microphone outlier.
Short coaching also has a retained 10.512-second completion. Complete numeric
artifacts report mean, SD, range and all boundary observations for every metric.
Do not generalize this single 30-sample-per-workload run to a production p90 SLA.

## Safety and reliability assessment

| Boundary | Existing behavior | Requirement before incremental playback |
|---|---|---|
| Validation and sanitization | Whole file decodes and metadata is stripped before delivery | Never send raw provider bytes to the learner; decode, strip tags and validate every emitted unit under resource/time caps. Later corruption cannot invalidate already heard sound, so explicit partial-failure recovery is required. |
| Format | One MP3 file, one decoder | Account for frame boundaries, bit reservoir, truncated tails and encoder delay; independently re-encoding successive prefixes and concatenating them can duplicate sound or introduce gaps. These probes are not a stitching design. |
| Authorization | Verified bearer identity and request-scoped repository; exercise/cue ownership | Keep authenticated same-origin fetch. Native audio src cannot replace bearer fetch. Bind the whole delivery and every continuation to verified owner and cue/revision; handle expiry/revocation policy explicitly. |
| Cancellation | Browser aborts fetch, pauses playback and rejects late continuations | Close provider stream, sanitizer process and any queue on disconnect/end/account change; bound in-flight bytes and check revision before each emission. Authentication at request start alone does not settle a later End. |
| Cache | Complete sanitized blobs only, versioned terminal feedback | Partial prefixes must never enter the complete cache. Publish atomically only after successful full processing; abort/failure removes staging artifacts. Preserve complete-message replay and cache versioning. |
| Retry | Saved assessment and complete audio can be retried independently | After audible output, automatic retry from byte zero repeats coaching. Require an explicit restart/replay strategy; retries before output must not duplicate emitted content. |
| Recovery | Saved checkpoints remain authoritative | Partial playback cannot mark a cue heard, lesson ready or next exercise available. Resume must offer the entire saved cue and preserve completed assessment. |
| Serial playback | Coaching prepares during acknowledgment but waits to speak | A readiness event must not interrupt acknowledgment, exercise replay or current coaching. Queue cancellation must discard all stale buffers. |
| Save before coaching | Deterministic final result committed before feedback synthesis | Synthesize only the authoritative saved cue; never speculate an assessment or stream evaluator notes. TTS completion/abortion must not alter adaptation. |
| Transcript gate | Owned, successfully completed assessment required even for guessed URLs | Keep server-side gates for text/audio and legacy evaluation. Neither provider metadata nor partial-error payloads may expose scripts or expected answers before completion. |

Application security and recovery can in principle be retained with validated
emission and explicit state handling, but this experiment proves only sanitized
independent prefix playback. It does not establish all requirements above. A raw
proxy streaming shortcut would violate the current boundary and is rejected.

## Recommended next slice and exact gates

First run an **offline paired whole-file sanitizer experiment**, retaining buffered
playback and the current decoder/re-encoder settings. Investigate piping the input
to the same ffmpeg invocation rather than writing the source file. This is a small,
reversible research change; do not assume it improves speed. Keep output validation,
metadata stripping, format, duration cap, error handling and timeout/process cleanup.
It can save only part of the measured sanitization time, not the provider synthesis
or assessment time. If the gain is negligible, close the experiment without adoption.

Acceptance for that experiment:

1. At least 30 alternating paired measurements per synthetic workload, same audio,
   ffmpeg build and host; retain failures/outliers and report p50/p90, SD/range.
2. At least 100 ms and 20% paired median sanitization reduction for both coaching
   workloads; no p90 regression above 50 ms. These are proposed engineering gates,
   not previously agreed product targets. Do not adopt a few-millisecond gain.
3. Zero newly accepted malformed/tag-bearing invalid output and no loss of metadata
   removal, decodability, speed/duration or complete-message behavior. Cover truncated
   and corrupt MP3, injected metadata, oversized/long streams, timeout, cancellation
   and subprocess cleanup. Do not claim byte equivalence if encoder containers differ.
4. Before any production adoption, pass full backend/SQLite and configured Postgres,
   frontend helper/collector, Chromium, lint/typecheck/build checks. Preserve auth,
   ownership, stale revision, serial playback, saved assessment and transcript tests.
   A missing live Postgres environment remains an explicit unverified gate.
5. Obtain separate approval for production implementation; then collect at least
   30 matched microphone turns per workload/cache group with first meaningful
   playback and separate audible observations. Report total-turn p50/p90 without
   inferring an 8-second or 5-second target from these synthetic TTS measurements.

Incremental playback remains a separate decision. Before approving a production
pipeline, require at least 30 repetitions per workload of **live sanitized** delivery,
at least 500 ms median earlier complete meaningful coaching phrase for both coaching
workloads, no p90 total-turn regression, zero duplicate/gapped/overlapping playback
in tested browsers, human acceptance of naturalness, and every safety/recovery row
above verified with injected failures before/after first sound. Measure buffering,
proxy behavior, concurrent sessions, resource caps, retries, cost and cache writes.
This feasibility study does not satisfy those gates or authorize implementation.

## Reproduction and artifacts

```bash
AUDLI_TTS_FEASIBILITY=1 .venv/bin/python -m scripts.benchmark_tts_feasibility \
  --samples 30 --output data/tts-feasibility-new-run
PLAYWRIGHT_BROWSERS_PATH=/tmp/audli-browsers node scripts/benchmark_tts_browser.mjs \
  data/tts-feasibility-new-run
.venv/bin/python -m scripts.analyze_tts_feasibility data/tts-feasibility-new-run
```

Use a fresh directory; provider requests are billed. Existing private Settings
loads credentials without printing them. Study scripts accept only built-in texts
and require development settings and the original model. Numeric and synthetic
audio artifacts are ignored in `data/tts-feasibility-20261009-live/`; share no `.env`.
`manifest.json`, `samples.jsonl`, `summary.json`, `browser.json`, `analysis.json`
and `joined-samples.jsonl` record method and complete observations. Synthetic full
and sanitized-prefix MP3 files permit replay. Compact findings also have a local
SHA-256 manifest. The sandbox-denied run produced no completed samples and was
terminated; live results come exclusively from the approved network-enabled run.

## Verification

Backend: 440 passed, including 28 compact research tests, SQLite persistence,
assessment boundaries, uncertainty, transcript gates, ownership, feedback and
audio validation. Frontend helpers: 51 passed. Python compilation and Node syntax
checks pass. No frontend or tracked production source changed; production build,
lint/typecheck and full application Chromium suite were not rerun for offline
research files. No live Postgres test URL was configured. Browser artifact playback
passed for 180/180 full/prefix artifacts. Whitespace and working-tree checks pass;
all changed files are untracked offline research scripts/tests/docs, and the tracked
diff is empty. The local backend/frontend processes were preserved.
