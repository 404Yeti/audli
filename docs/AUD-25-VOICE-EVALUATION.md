# AUD-25 — Audli voice evaluation package

Research date: **9 October 2026**; live pilot completed **10 October 2026 UTC**.
Owner-review package; no production change, commit, merge or deployment.

## Controlled production rollout — owner approved 10 October 2026

Release branch: `feat/AUD-25-production-tutor-tts`, based on current deployed
`main` (AUD-24). The prior evaluation branch predates AUD-24 and contains unrelated
latency work; only the tutor-provider patch and AUD-25 artifacts were transferred.
AUD-24's welcome, accent preferences and passage behavior are preserved.

Reviewed release checks: **468 backend tests passed**, frontend tests, lint and
production build passed. Browser regression: **92 passed, 1 skipped** (real-backend media integration requires its separate fixture); focused provider/budget/AUD-24/research-runner suite: **57 passed**. Production dependency audit returned zero vulnerabilities.
Original evaluation branch regression suite also passed (461 tests). Required
GitHub branch protection is absent; PR checks will be inspected before merge.

Pre-activation provider reads returned HTTP 200 for account, subscription, custom
voice and models. Creator usage was **92,077 / 121,314 credits**, leaving **29,237**;
both overage flags were false. Custom voice ID and Flash model support match the
approved selection. Prior paid local requests established Text to Speech access
for the supplied local key. The current read API does not expose key-specific
permissions or credit limit, so production secret installation and a bounded
key limit need owner confirmation. No credentials were printed or committed.

Render workspace: `robert's workspace` (`tea-dauqlhd9fdbs739vmkv0`).
Service: `audli-api` (`srv-db270erbc2fs73fg85gg`),
`https://audli-api.onrender.com`. Before merging, the tutor selector was explicitly
set to `openai` through a merge-only environment update; existing origins and all
other variables were preserved. This triggered configuration deployment
`dep-db4pf6vlot8c73chqmig`, which went live on the existing AUD-24 commit.
Baseline 18 public production probes passed: backend/proxy health 200; permitted
custom/Vercel origins reach auth (401 without credentials); unauthorized/HTTP/
lookalike/opaque/wrong-port origins remain 403; invalid tokens/protected reads
remain 401; preflight remains fail-closed without wildcard CORS.

Production activation and paid authenticated smoke testing are pending the
Coral-first code deployment verification, securely saved production key and
dedicated beta-approved test account token. Planned smoke ceiling: at most six
new tutor cue requests, at most 1,000 server-owned spoken characters and 12 total
synthesis attempts including fallback, no automatic retries; estimated ElevenLabs
usage ceiling $0.04. Existing cached replays must add zero synthesis usage.
Any uncertain failure consumes its reserved attempt and is not retried.
Do not intentionally invalidate global production credentials or introduce a
provider outage to test fallback: use injected-failure regression evidence and
observe live fallback if a natural failure occurs. Live forced-failure acceptance
must be distinguished from mocked tests. Keep Coral selected if prerequisites
remain missing. Real iPhone and learner-facing acceptance are still required;
AUD-25 must remain In Progress.

## Optional tutor provider implemented locally — 10 October 2026

The owner completed the blind review and selected **Audlis voice**, ElevenLabs
`yKYzqEa22xh5PdidhN70`. This selection supersedes the historical audition and
owner-review recommendations below. Implementation is on
`feat/AUD-25-optional-tutor-tts`, uncommitted. Nothing was merged, deployed or
enabled in production. Coral remains the default and the fallback.

### Implementation and activation

`app/services/tutor_tts.py` wraps the existing AI provider: tutor `speak` calls
can use ElevenLabs; passage `speech`, generation, evaluation and transcription
remain delegated unchanged. All tutor routes use the same adapter, including
welcome, check-ins, acknowledgment, coaching, explanation and retry speech.
The selected voice is backend configuration, never a browser-supplied voice ID.
Existing MP3 responses, server ownership, transcript gates and persistence stay
in place. No frontend or learner-state changes were made.

To activate after approval, configure the backend process:

```dotenv
AUDLI_PROVIDER=openai
AUDLI_TUTOR_TTS_PROVIDER=elevenlabs
AUDLI_ELEVENLABS_MODEL=eleven_flash_v2_5
AUDLI_ELEVENLABS_VOICE_ID=yKYzqEa22xh5PdidhN70
# ELEVENLABS_API_KEY supplied through backend secret configuration
# Keep existing OPENAI_API_KEY for Coral fallback and other OpenAI functions.
```

The application reads `ELEVENLABS_API_KEY` from process environment or its
configured environment file; it does not hardcode the owner's private file path.
The local probe explicitly merged `.env` with
`/home/lenovo/.config/audli/elevenlabs.env` at runtime, without logging credentials.
Default generation settings: stability 0.5, similarity boost 0.75, style 0,
speaker boost true. Their configuration names are
`AUDLI_ELEVENLABS_STABILITY`, `AUDLI_ELEVENLABS_SIMILARITY_BOOST`,
`AUDLI_ELEVENLABS_STYLE`, `AUDLI_ELEVENLABS_SPEAKER_BOOST`.
Existing tutor speaking rate is retained (0.9 in the measured cues).
`AUDLI_TUTOR_TTS_TIMEOUT_SECONDS=15` bounds ElevenLabs synthesis plus validation;
`AUDLI_TUTOR_TTS_FALLBACK_TIMEOUT_SECONDS=20` bounds Coral synthesis plus validation.
Rollback is `AUDLI_TUTOR_TTS_PROVIDER=openai`. Passage configuration is unchanged.

There is one ElevenLabs attempt and at most one Coral fallback, with SDK/transport
retries disabled for tutor synthesis. Cancellation closes the active stream and
never starts fallback; cancelled ffmpeg validation is killed and awaited.
Provider errors are replaced by safe messages without payload-bearing exception
chains. Sanitized complete MP3 is returned, not mixed partial audio.
Cache identity includes provider/model/voice/settings/text and remains scoped to
account or conversation. Coach cache identifiers fit the existing 80-character
column. Dedicated audio locks suppress simultaneous identical cache misses in
one process without blocking the assessment state lock.

Structured `Tutor TTS` events use the existing Uvicorn logger with numeric and
enumerated fields: first audio byte, synthesis duration, validated readiness,
input character count, available reported cost units, outcome, HTTP status and
fallback events. No text, user IDs, keys or audio are logged.

### Capped local end-to-end evidence

Artifacts: `data/voice-evaluation/aud25-implementation-20261010/`.
`playback.json` records timings and `budget.json` records reservations before
provider requests. Reusable explicit-opt-in harnesses are
`scripts/verify_tutor_tts.py` and `scripts/verify_tutor_playback.mjs`.
They are local-only; harness endpoints are not registered in the production app.

Enforced limits were 6 total paid attempts, 600 total input characters,
2 ElevenLabs attempts and $0.02 estimated ElevenLabs usage, with no automatic
retries. Actual use was **4 attempts / 280 characters**, including
**2 ElevenLabs attempts / 140 characters** and 2 Coral attempts / 140 characters.
ElevenLabs returned **31 cost units** (27 + 4); the conservative $0.04/1,000
character estimate is **$0.0056**. This is an estimate, not an invoice or a new
subscription balance reconciliation. OpenAI did not return billed usage for these
speech responses, so actual Coral cost remains unmeasured. Four subsequent cache
requests caused zero synthesis calls. All synthesis calls succeeded without
fallback or retries.

| Provider / cue | First audio byte | Complete synthesis | Validated API audio ready | Chromium playing | Cached response |
|---|---:|---:|---:|---:|---:|
| Audli / onboarding (122 chars) | 749 ms | 970 ms | 1,419 ms | 1,549 ms | 32 ms |
| Audli / acknowledgment (18 chars) | 236 ms | 238 ms | 476 ms | 551 ms | 21 ms |
| Coral / onboarding (122 chars) | 14,391 ms | 15,248 ms | 15,555 ms | 15,618 ms | 26 ms |
| Coral / acknowledgment (18 chars) | 1,414 ms | 2,144 ms | 2,367 ms | 2,425 ms | 19 ms |

Audio files:

- `elevenlabs-onboarding.mp3`
- `elevenlabs-acknowledgment.mp3`
- `openai-onboarding.mp3`
- `openai-acknowledgment.mp3`

Listen to each matched cue at the same volume, checking clarity, warmth and
natural pacing; these implementation samples are provider-labelled because the
owner has already completed the blind selection. Timings use identical text and
rate. API readiness includes decoding/sanitization; browser readiness measures
request start to the Chromium `playing` event. This is a single local observation
per cue, not acoustic onset, a percentile benchmark or an iPhone guarantee.
The large first Coral observation may be transient and does not establish a
lasting provider speed difference. The harness used a synthetic authenticated
account and the real FastAPI audio routes, not production Supabase, Render, the
full Next.js UI or a complete microphone-driven lesson.

### Verification and remaining acceptance

Full backend suite: **461 passed**. Frontend `npm test` passed, including recording,
1.8-second silence, serial playback and cancellation coverage. New tests cover
selection, MP3 validation, safe fallback, bounded timeout, cancellation without
fallback, no SDK retries, account isolation, transcript gates, configuration/text
cache invalidation, concurrent cache reuse and reservation-before-request caps.
Final targeted suite after logging integration: **15 passed**.

Remaining before production acceptance: owner approval, backend secret/config
setup, account commercial entitlement and custom-voice retention review, and a
real authenticated browser/iPhone check of welcome, returning check-in, coaching,
encouragement, explanations, retry, cancellation, completed-assessment durability
and serial playback. Verify passage voices stay unchanged, then exercise a safe
fallback and rollback check and confirm production metrics contain no sensitive
content. No accent implementation or authenticity claim was added.

Known limits: locks are process-local, so simultaneous misses across multiple
workers can still synthesize twice. A cached Coral fallback remains cached for
that cue/configuration, avoiding repeated billed failures. Cancellation/timeout
cannot guarantee reversal of a charge already accepted by the remote provider;
fallback may add one Coral charge. There is no new distributed lock or circuit
breaker. AUD-25 remains In Progress pending approved rollout and production
acceptance. Historical sections below describe the earlier research stage.

## Completed blind pilot — 10 October 2026

**22/22 samples generated, all HTTP 200, zero retries or failures.** Voice,
model and subscription reads all returned HTTP 200 after the owner updated key
permissions. Credentials were read at runtime from the private supplied file;
no credentials or learner data were printed, saved in artifacts or committed.
The Creator account had 30,380 included credits available before synthesis and
29,268 after the settled usage check. No overages or subscriptions were enabled.

One custom Audli match was found across the complete account voice listing
(`has_more=false`), and Get Voice confirmed it: **Audlis voice**, category
`generated`, description identifying Audli's listening tutor,
ID **`yKYzqEa22xh5PdidhN70`**. There was no ambiguity requiring owner selection.

| Tutor | Verified synthesis ID | Samples | Input characters | Median first audio bytes / complete body (ms) | First-byte range (ms) | Reported cost units | List-rate usage estimate |
|---|---|---:|---:|---:|---:|---:|---:|
| Audli custom | `yKYzqEa22xh5PdidhN70` | 4 | 798 | 178.5 / 518.1 | 152–2,279 | 174 | $0.03192 |
| Talia — Warm Soft Guide | `OZ0L6eISlOejga3XjDFt` | 4 | 798 | 179.8 / 506.2 | 163–3,171 | 174 | $0.03192 |
| Eddie — Natural and Helpful | `l7kNoIfnJKPg7779LI2t` | 4 | 798 | 171.8 / 524.1 | 165–691 | 174 | $0.03192 |
| Maisie — Friendly Casual Neighbor | `QtY3JBOUKEB5xzrRfOKc` | 4 | 798 | 169.4 / 486.6 | 169–674 | 174 | $0.03192 |
| OpenAI Coral | `coral` | 4 | 798 | 1,706.8 / 3,471.1 | 1,130–1,834 | Not returned | Invoice/token reconciliation pending |

Eddie's originally documented “Helpful and Comforting” name was absent. The
[official replacement table](https://elevenlabs.io/docs/help-center/product/voices/my-voices/what-are-default-voices)
links to a tracking page whose embedded destination is
`https://elevenlabs.io/app/voice-library?search=l7kNoIfnJKPg7779LI2t`.
The unique voice from the same publisher as Talia and Maisie is now named
“Eddie - Natural and Helpful.” This establishes identity through the official
link, rather than substituting another voice merely named Eddie. Five selected
shared voices were saved to the account collection to permit Get Voice and
synthesis; all save requests returned HTTP 200 and consumed no synthesis credits.
No existing voice was removed or production configuration changed.

### Scope, usage and spending

To include four identical-script custom-voice samples, the original six passage
slots became two (Australian and Irish). The final manifest has **18 ElevenLabs
attempts + 4 Coral attempts = 22 total**, stricter than the latest 22-ElevenLabs
attempt ceiling. **4,934 total input characters**: 4,136 ElevenLabs and 798 Coral,
below 6,024. All five tutors used the same welcome/mistake scripts at 1.0 and
0.85; passages used the same neutral 472-character passage at 1.0.

The projected ElevenLabs list-rate usage was **$0.20320**, below the approved
**$0.32232** estimate. No selected voice had a price multiplier above 1, and the
selected models were exposed as TTS-capable with cost factor 1. Guards reserve
attempts/characters before each POST, forbid automatic retries, check included
quota, refuse repeated execution with an exclusive lock, and honor a lower
`AUD25_MAX_ELEVEN_USD` if configured. Six guard tests passed (0.65s), including
the revised manifest and rejection of price multipliers/lower budgets.
[ElevenLabs current list rates](https://elevenlabs.io/pricing/api).

**Actual reported ElevenLabs usage: 1,112 cost units**, from all 18 response
`character-cost` headers. The immediate account delta was only 252; a later
read at **2026-10-10T00:03:29Z** settled to **1,112** (90,934 → 92,046), matching
the header total exactly. These are reported usage units, not input-character
counts or dollars. Included quota covered the run; the public-rate $0.20320
estimate is not an invoice charge. Coral's MP3 response returned no billed token
usage: dollar cost remains unreconciled, not zero. Do not fabricate token counts
from text characters. Its generated audio totals 58.68 seconds.

All outputs were decoded and sanitized with local ffmpeg; 22 file SHA256 hashes
were verified. Timing uses a monotonic clock on the local WSL host, one serial
HTTP client, no retries. Tutors use `eleven_flash_v2_5`; passages use
`eleven_multilingual_v2`; Coral uses the existing `gpt-4o-mini-tts`, MP3 and exact
current instructions. Custom samples ran after the original tutor block, so
order/connection effects were not counterbalanced. The sample counts are four
different text/rate conditions per tutor, not repeated reliability trials.
First body bytes are not first audible speech or iPhone/production turn latency.
No p90 claim, listening-quality ranking or final voice choice is justified.

### Accent mapping and remaining gaps

| Intended category | Candidate and ID | API metadata claim | Pilot evidence / gap |
|---|---|---|---|
| General American | Spuds Oxley — Old Storyteller; exact ID unresolved | Original public label American, broader than GA | No exact name match; differently named Grandpa entry was not substituted; no generated passage |
| British English | Christopher — British Storyteller; exact ID unresolved | Original public label British | No exact name match; regional variety and identity unresolved; no generated passage |
| Australian English | Clara, `tyepWYJJwJM9TTFIg5U7` | English Australian | One passage, 472 chars, 208 cost units; first bytes 2,787.0 ms / complete 5,373.5 ms; authenticity pending listening |
| Irish English | Jessica Gallagher, `DbwWo4rVEd5NrejHYUnm` | English Irish and en-british across metadata variants | One passage, 472 chars, 208 cost units; first bytes 2,226.3 ms / complete 3,864.8 ms; mixed claims warrant listening |
| Scottish English | Isla, `h8eW5xfRUGVJrZhAFxqK` | English Scottish and northern | Exact library ID resolved; passage deferred to preserve custom-voice budget; authenticity unverified |
| Indian English | Maya, `4O1sYUnmtThcBoSBrri7` | English Indian | Exact library ID resolved; passage deferred to preserve custom-voice budget; authenticity unverified |

API “verified_languages” metadata is a provider claim, not our acoustic evidence.
**All six accent categories remain unapproved.** No listening analysis or human
ratings have yet been supplied. Tutor identity, accent selection and difficulty
remain independent. Creator tier is confirmed; commercial output still requires
compliance with current terms, non-beta eligibility and underlying voice rights.
No permanent-availability guarantee is inferred from account access.

### Owner listening package

Local folder: **`data/voice-evaluation/aud25-resolved/blind/`**, containing
**`B001.mp3` through `B022.mp3`**, `README.md`, blank `ratings.csv`, and a local
`review.html` audio player. The ZIP **`data/voice-evaluation/aud25-resolved/blind-audition.zip`**
contains only these blind-review files, never voice mappings or credentials.
Extract the ZIP before opening review.html. Read README, listen in randomized
order with the same headphones/volume, score 1–5 and record word errors, pace,
warmth and accent impressions before unblinding. Familiarity with a voice may
still reveal its identity; neutral filenames do not guarantee perfect blinding.

Keep `discovery.json`, `results.json`, `summary.json` and `measurements.csv`
outside the blind folder until ratings are saved. They contain provider/voice
identity, request IDs, usage, timings and the label mapping. `results.json` is the
complete spending ledger, including initial and delayed quota evidence.

Next step: owner blind audition, then review ratings and the unresolved accent
identities. No further synthesis is authorized by this completed run; shortlist
repeat trials only after review. Keep Coral available and production TTS unchanged.
AUD-25 remains In Progress pending owner review; no voice was automatically chosen.

## Historical blocked preflight — supplied credential, 9 October 2026

The credential at `/home/lenovo/.config/audli/elevenlabs.env` is readable at
runtime, and the selected `ELEVENLABS_API_KEY` matches that file. Its value was
never printed or persisted. The OpenAI baseline credential is also accessible.
All four local spending-guard tests passed (`4 passed in 0.56s`). The authorized
manifest remains capped at **22 synthesis attempts / 6,024 input characters / no
automatic retries**, with an ElevenLabs base-rate estimate of **$0.32232** plus
Coral usage and any account-specific pricing. This is an offline estimate, not a
verified account quote; the included-quota gate must succeed before spending.

The first real metadata request failed with HTTP 401. Two subsequent read-only
diagnostic requests confirmed **`missing_permissions`** on both `GET /v2/voices`
and `GET /v1/user/subscription`. These were deliberate metadata diagnostics, not
automatic retries or synthesis requests. Sandbox inspection was interrupted
without a response; the external-network inspection received the rejection.

**Actual synthesis usage: 0 attempts, 0 input characters, $0 synthesis cost.**
No audio was generated. Synthesis latency, per-call credit usage, account quota,
commercial entitlement, and exact Talia/Eddie/Maisie IDs remain unmeasured or
unverified. Coral was not run alone while the comparative pilot was blocked.

| Candidate group | Planned samples | Generated | Latency | Verified voice IDs |
|---|---:|---:|---|---|
| Talia | 4 | 0 | Not measured | Blocked by voice-read permission |
| Eddie | 4 | 0 | Not measured | Blocked by voice-read permission |
| Maisie | 4 | 0 | Not measured | Blocked by voice-read permission |
| Coral | 4 | 0 | Not measured | Configured `coral`; no new sample |
| Six accent candidates | 6 | 0 | Not measured | Unverified; coverage matrix below |

Non-secret API evidence:
`data/voice-evaluation/aud25-live-20261009-network/auth-diagnostic.json`.
This directory contains diagnostics only, no listening samples or completed
discovery manifest. All six intended accent categories remain **acoustically
unverified**; the existing candidate mapping below is unchanged, including the
American-versus-General-American gap and the weak Irish/Scottish/Indian claims.

**Smallest owner action:** edit this key's permissions in ElevenLabs to allow
voice/library reads and user/subscription reads, plus model reads and Text to
Speech for the pilot. Keep other permissions restricted and keep existing
spending limits; do not enable recurring overages. Only the first two read
failures were directly confirmed; model/TTS access has not been tested.
ElevenLabs supports restricted permissions and usage limits in its
[API-key settings](https://elevenlabs.io/docs/overview/administration/workspaces/api-keys).
Save any replacement credential to the same private local file, never chat or
the repository. Reinspect in a fresh output directory, then run the unchanged
capped pilot after IDs and included quota pass. AUD-25 remains In Progress.

## Earlier pilot execution attempt — before credential path was supplied

The owner authorized the existing 22-request pilot and reported that an ElevenLabs
key was configured locally. This agent session could not read it: no ElevenLabs
variable in the process environment, no `ELEVENLABS_API_KEY`/`XI_API_KEY` or other
Eleven-related variable in repository `.env`; `.bashrc` and `.profile` contain no
ElevenLabs setup. The local OpenAI credential is nonempty. No keys were printed.
The key's configured path and variable name have been requested, without requesting
the secret itself. Credentials outside those locations have not been searched indiscriminately.

**Measured production-provider results: none.** The live runner exited before
metadata or synthesis: `ElevenLabs credential unavailable; no API calls made`.
There is no `data/voice-evaluation/aud25-live-20261009/` directory or new audio.
Actual request usage is zero for this attempted run, with zero synthesis charges;
voice IDs, account entitlement and accent mapping remain unverified.

| Candidate | Planned samples | Verified ID in this account | Measured first bytes / total | Actual usage |
|---|---:|---|---|---|
| Talia | 4 | Pending credential access | Not measured | 0 calls |
| Eddie | 4 | Pending credential access | Not measured | 0 calls |
| Maisie | 4 | Pending credential access | Not measured | 0 calls |
| Coral | 4 | Existing configured voice `coral`; not newly auditioned | Not measured | 0 calls |
| Six accent candidates | 6 | Pending credential access | Not measured | 0 calls |

The unchanged offline estimate is **$0.32232 ElevenLabs base-rate usage**, plus
Coral/account minimums/tax/custom rates. Current public API pricing was rechecked;
account-specific usage/pricing cannot be confirmed without the credential. Hard
limits in `scripts/run_voice_evaluation.py`: 22 synthesis attempts, 6,024 reserved
input characters (the exact pilot), 10,000 observed ElevenLabs cost units, no
automatic retries, a fresh included-quota check and refusal to enable overages.
Failures reserve requests/characters before network access, and an exclusive run
lock prevents a subsequent invocation from repeating ambiguous billed calls.
Metadata discovery is separately capped at 50 GET requests.

The local runner has two modes; use the venv and a private env file if needed:

```bash
.venv/bin/python -m scripts.run_voice_evaluation inspect \
  --env-file /private/path/to/credentials.env --output data/voice-evaluation/new-live-run
.venv/bin/python -m scripts.run_voice_evaluation run \
  --env-file /private/path/to/credentials.env --output data/voice-evaluation/new-live-run
```

Inspection requires exact names/unique matches, confirms IDs by Get Voice and
records non-secret metadata. It excludes custom-rate search results. Missing or
ambiguous tutor IDs block synthesis; missing accent IDs are explicitly skipped,
never replaced by a guessed voice. Model compatibility and scheduled removals
are checked where exposed. Metadata claims still do not certify accent authenticity.
Usage headers and account quota deltas are recorded separately; account deltas can
include concurrent activity or lag. OpenAI MP3 responses do not establish actual
billed token totals, so invoice reconciliation may still be required. Character/
request limits are hard local guards; the USD estimate is not a provider billing guarantee.

On success, sanitized metadata-free MP3s are saved to `<run>/blind/Bnnn.mp3`;
`<run>/blind/ratings.csv` is the blank owner rating sheet. Labels are shuffled
independently of provider order. Keep discovery/results.json out of the blind
listening folder because they contain the voice mapping. Listen before opening
the unblinding results, rate warmth/naturalness/intelligibility/pronunciation/
expression/pace, and record accent impressions without treating labels as proof.
No successful audio paths can be provided until the credential location is resolved.

The runner's budget and failure behavior are validated using synthetic HTTP
responses, not real provider calls. This establishes spending guards only, not
ElevenLabs compatibility, voice quality or actual latency. Production TTS is unchanged.

## Recommendation and evidence limits

Audli needs a steady, patient listening teacher. Review the completed blind pilot
with **the custom Audli voice, Talia, Eddie and Maisie against OpenAI Coral**.
These are research priorities based on
provider descriptions, **not listening winners**. Retain Coral until identical-text
tests demonstrate better teaching delivery, acceptable cost and dependable access.
Evaluate accent passage speakers separately; do not use the tutor's accent as an
implicit lesson setting.

The live pilot above supplies audio, verified tutor IDs and measured latency.
There are **no human ratings or independently verified accents** yet. Public
pages and account metadata establish candidates, not acoustic authenticity.
Reserve tutor IDs and unresolved American/British passage IDs remain pending.

## Tutor candidate matrix

All eight are listed in ElevenLabs' current replacement-voice table. That table
also says older Default voices expire **31 December 2026**, and are restricted to
accounts created before March 2026. Its replacement availability promise is a
provider statement, not a contractual SLA. Avoid selecting old Sarah, Matilda,
Jessica, Brian or Alice as a new permanent identity.
[Official replacement list and voice links](https://elevenlabs.io/docs/help-center/product/voices/my-voices/what-are-default-voices).

The descriptor column contains provider labels; the remaining columns are our
test hypotheses. Accent, pronunciation, normal/slower-rate quality, consistency,
latency and account availability are **pending for every row**.

| Candidate | Provider descriptor | Teaching hypothesis | Specific audition risk | Priority |
|---|---|---|---|---|
| Talia | Warm Soft Guide | Patient reassurance | Soft delivery may hide word endings | Pilot |
| Eddie | Helpful and Comforting | Friendly correction | May sound overly reassuring | Pilot |
| Maisie | Friendly Casual Neighbor | Natural small talk | Casual reductions may reduce beginner clarity | Pilot |
| Caleb | Trusted Guide | Confident task guidance | Authority may feel formal | Reserve |
| Kellan | Casual Friendly Speaker | Relaxed conversation | May sound too informal | Reserve |
| Jade | Upbeat and Natural | Encouraging delivery | Excess enthusiasm during correction | Reserve |
| Lawrence | Bright and Informative | Clear explanations | Potential presenter cadence | Reserve |
| Alicia | Polished Global Anchor | Pronunciation control | Potential newsreader formality | Reserve |
| OpenAI Coral | Current Audli control | Known integration and recovery path | Must re-rate blindly; familiarity is not evidence | Control |

For previews, use the linked names in the official replacement table, or search
the exact descriptor in [ElevenLabs Voices](https://elevenlabs.io/voice-library).
These are official preview/discovery entry points; they may require login.
They are not direct MP3 URLs and were not listened to in this run. Coral's official
guide links its interactive preview: [OpenAI speech guide](https://developers.openai.com/api/docs/guides/text-to-speech).

For each voice, record voice ID, creator, category, accent label, preview URL,
available tiers, permitted base models, notice period, removal date, price multiplier
and live moderation. The [voice search API](https://elevenlabs.io/docs/api-reference/voices/search)
exposes metadata including preview URLs and model/verified-language information.
That metadata remains a claim until listening tests confirm it.

## Accent candidate matrix

Every row is **unverified**, with no acoustic score. These names come from the
specific “Most popular voices” lists, not the generic recommendation cards below
them. Those cards mix unrelated regions, so merely appearing on an accent page
is weak evidence. Record each speaker's claimed region and English sample history.

| Target | First audition candidate / official preview page | Backup discovery candidate | Main unresolved question |
|---|---|---|---|
| General American | [Spuds Oxley — Old Storyteller](https://elevenlabs.io/text-to-speech/american-accent) | Peter — Confident Storyteller | American label is broader than General American; age/storytelling could exaggerate rhythm |
| British English | [Christopher — British Storyteller](https://elevenlabs.io/text-to-speech/british-accent) | Bradford — Articulated Storyteller | Identify actual UK regional variety; narration versus everyday speech |
| Australian English | [Clara — Australian Professional Female with Warmth & Trust](https://elevenlabs.io/text-to-speech/australian-accent) | Lee — Middle-Aged Australian Male | Confirm vowel patterns persist without accent prompts |
| Irish English | [Jessica Gallagher — Clear and Neutral](https://elevenlabs.io/text-to-speech/irish-accent) | Rory — Natural, Warm and Lively | Neutral label may mean accent attenuation; specify region |
| Scottish English | [Isla — Youthful, Relaxed, and Warm](https://elevenlabs.io/text-to-speech/scottish-accent) | Adam — Classic Scottish Storyteller | Distinguish regional Scottish English from theatrical caricature |
| Indian English | [Maya — Friendly and Cheerful](https://elevenlabs.io/text-to-speech/indian-accent) | Monika Sogam — Deep and Natural | Confirm English rather than only Indian-language capability; identify speaker's region |

**No accent is approved yet.** Irish, Scottish and Indian coverage is particularly
uncertain without account metadata and actual same-passage audio. General American
also needs a narrower classification than “American.” Do not confuse Irish-language
model support with Irish English. Duplicate names such as Adam, Clara, Peter and
Patrick refer to potentially different voices; IDs and creators are mandatory.

For authenticity, invite two reviewers familiar with each regional variety; add
a phonetics-informed reviewer where opinions conflict. Capture time-coded vowel,
consonant, stress and rhythm observations without treating any one sound as proof.
The neutral passage includes water, coffee, park, train, Thursday, market and
half past three; it offers comparison points, not a diagnostic accent classifier.
Judge within-speaker consistency across the beginning/middle/end and a second
generation. Natural regional diversity is valid; clarity and authenticity are
separate scores. Use beginner and advanced listeners to assess intelligibility.

## Fixed scripts and experiment artifacts

[scripts.json](voice-evaluation/scripts.json) is the immutable source of seven
original tutor scripts and one neutral English passage. It covers welcome, goals,
task instructions, clarification, encouragement, correction and small talk.
[candidates.json](voice-evaluation/candidates.json) contains discovery metadata.

The clarification script represents uncertainty about what the learner meant;
it is not terminal feedback. The mistake and encouragement scripts assume a
completed, reliable assessment and contain no evidence requests. They illustrate
delivery only, not new evaluator logic. Passage and correction text are synthetic
and must not be presented to learners before existing backend gates permit them.
Set the owner-approved pronunciation of “Audli” as an audition check before choosing
a voice; do not conceal a mismatch by rewriting only one candidate's script.

The offline planner imports no application code, reads no credentials, makes no
network requests and refuses to overwrite existing experiment directories:

```bash
python3 scripts/plan_voice_evaluation.py --output data/voice-evaluation/new-pilot
python3 scripts/plan_voice_evaluation.py --stage full --max-characters 40000 \
  --output data/voice-evaluation/new-full
```

Already prepared locally (under ignored `data/`, **plans only, no audio**):

| Plan | Content | Requests | Characters | ElevenLabs estimate only |
|---|---|---:|---:|---:|
| `data/voice-evaluation/aud25-pilot/` | 3 tutors + Coral; welcome/correction at 1.0 and 0.85; 6 accent passages at 1.0 | 22 | 6,024 | $0.32232 |
| `data/voice-evaluation/aud25-full/` | 8 tutors + Coral; all 7 scripts at both rates; 6 accent passages | 132 | 24,468 | $0.99584 |

Each directory contains `manifest.json`, blank `ratings.csv` and a measurements
header. Each planned sample has an ID, text hash and intended `audio/Snnn.mp3`
path. **Those MP3 files do not exist.** Keep the manifest hidden from blind raters;
randomize playback order independently for each rater and use only opaque IDs.
These one-generation plans screen quality; they do not establish consistency or p90.

## Repeatable listening, latency and budget methodology

1. Resolve exact voice IDs and account access; save non-secret metadata and dated
   pricing. Freeze texts, model, voice settings, output codec, locale, date and host.
   Use MP3 at a common supported quality, initially `mp3_44100_128` for ElevenLabs.
   For Coral use Audli's exact current instructions, MP3 and model alias. Record
   any differences; identical speed values need not produce identical words/minute.
2. Pilot rates: 1.0 and 0.85; add a Coral 0.9 production-control run later. Apply
   numeric speed once, never combined with “speak slowly.” Starting ElevenLabs
   stability/similarity/style values are in scripts.json, not optimized findings.
   Verify settings support per model. Measure actual duration and words/minute.
   ElevenLabs documents speed 0.7–1.2; extremes can harm output.
   [Pace guidance](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices).
3. Generate serially with provider/voice order interleaved; no automated retries.
   Count failed calls toward the budget. Stop on auth/rate-limit errors or spend
   threshold. For this run enforce **22 total attempts, 6,024 total input
   characters and the approved $0.32232 ElevenLabs base-rate estimate ceiling**;
   owner account limits are additional protection, not a guaranteed instantaneous
   cutoff. Subscription minimums, voice surcharges and Coral costs are extra.
4. ElevenLabs initial tutor endpoint: POST `/v1/text-to-speech/{voice_id}/stream`,
   `model_id=eleven_flash_v2_5`, `voice_settings` including `speed`, `xi-api-key`
   from server-local environment. Accent endpoint is the same with
   `eleven_multilingual_v2`. Coral: POST `/v1/audio/speech` with Bearer credential,
   `model=gpt-4o-mini-tts`, `voice=coral`, `response_format=mp3`, `speed`, current
   instructions. Stream and retain complete output for both. Follow the current
   [ElevenLabs stream reference](https://elevenlabs.io/docs/api-reference/text-to-speech/stream)
   and [OpenAI speech reference](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create).
   The local runner has now completed the capped live pilot; it is independent
   of the production TTS adapter.
5. Use a monotonic clock: request start; response headers; first nonempty audio
   body bytes; complete body; complete decoded/sanitized audio ready. Record
   failure, HTTP status, request ID, byte count, duration, SHA256 and connection
   state in measurements.csv. Never log credentials or provider error bodies.
   First bytes are not first audible or meaningful speech. Decoder work and the
   browser's `playing` event must be measured separately. Do not change Audli's
   validation/buffering path to obtain attractive benchmark numbers.
6. Once quality finalists exist, repeat each selected model/voice/rate/text at
   least three times for consistency; use at least 20 trials per latency workload
   before descriptive p50/p90, preferably 30. Include cold/warm connections and
   errors, report median and nearest-rank p90 by workload with sample counts.
   From the same host compare a synthetic render-region run separately from a
   desktop run. iPhone tap-to-play, full turn latency and microphone-to-feedback
   require later device tests; provider inference claims are not those timings.
7. Rate 1–5: 1 unacceptable, 3 usable with concerns, 5 excellent. Weight tutor
   intelligibility/pronunciation 30%, naturalness 20%, warmth 20%, appropriate
   expression 10%, pace 10%, consistency 10%. Keep pronunciation notes separately
   even within the first combined score. Report beginner and advanced cohorts
   separately; do not hide subgroup failures in a mean. Use at least three listeners
   per cohort, blind and randomized. Check word insertions/omissions, number/name
   errors, exaggerated cheerfulness, artificial pauses, whispering and voice drift.
8. Selection proposal: no meaning-changing errors; median intelligibility and
   warmth at least 4 in both cohorts; no accent drift; improvement over Coral in
   blind preference; no material p90 regression; owner-accepted session cost and
   continuity terms. Thresholds are proposed owner-review criteria, not proven results.

## Model, pricing and cost findings

The live **ElevenAPI** page, not older Creative credit tables, lists these rates
on the research date. Plans: Starter $6/month, Creator $22, Pro $99, Scale $299,
Business $990; a PAYG option is also listed. Tax excluded; check the actual account
quote and commercial entitlement before buying.
[Current API pricing](https://elevenlabs.io/pricing/api).

| Model | USD / 1,000 characters, regular | Research role |
|---|---:|---|
| Flash / Turbo | $0.04 | Flash v2.5 first tutor benchmark |
| Multilingual v2 | $0.08 | Accent passage baseline |
| v3 | $0.08 | Optional expressiveness challenger |
| v3 Conversational | $0.04 | Optional newer conversational challenger |
| v4 | $0.08 | Newly listed; optional quality challenger |
| v4 Turbo | $0.04 | Newly listed; optional latency challenger |

v4/v4 Turbo promotional rates are $0.022/$0.011 until October 12. Do not base a
permanent voice decision on a three-day promotion. Budget plans use regular rates.

Model docs describe Flash v2.5 inference around 75 ms and v4 Turbo median inference
around 100 ms, excluding application/network overhead. Multilingual v2 prioritizes
stable long-form quality; Turbo v2/v2.5 are deprecated in favor of Flash. v4 uses
Text-to-Dialogue API and v4 Turbo a dialogue WebSocket; they are not drop-in
choices for the pilot REST protocol. Verify rollout, account access and beta status.
Language coverage does not certify an English regional accent.
[Models and endpoint differences](https://elevenlabs.io/docs/overview/models).

Cost scenario, **assumptions rather than observed Audli usage**: 2,000 tutor
characters + 4,000 passage characters in one learner session; no cache hits,
retries, surcharges or tax. Flash-only is **$0.24/session**; tutor Flash + passages
Multilingual v2 is **$0.40/session**; all Multilingual v2 is **$0.48/session**.
Thirty such sessions cost $7.20/$12.00/$14.40 in list-rate usage per learner.
One-time welcome and replay caching can reduce new synthesis, but model/voice
changes invalidate relevant caches. Do not double-count subscription allowances:
actual spend is plan minimum plus billable usage outside included allowance under
the chosen account contract, not automatically subscription plus every listed dollar.

Coral uses a different billing unit: the current model page lists **$0.60 per
million text input tokens and $12 per million audio output tokens**. Formula:
`0.60 * input_tokens / 1e6 + 12 * output_audio_tokens / 1e6`.
For an illustrative session with 2,000 billed input tokens and 10,000–30,000 audio
tokens, cost is $0.1212–$0.3612. **Those token counts are scenario inputs, not
measured equivalents of the 6,000-character ElevenLabs session**; no cheaper-provider
claim follows. Export billing usage for the identical scripts and rates before a
matched cost comparison. The frequently repeated $0.015/minute estimate was not
confirmed on the current fetched pricing page and is not used here.
[Coral model pricing](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts).

## Latency evidence

| Evidence | First audio | Total synthesis | Interpretation |
|---|---|---|---|
| New ElevenLabs experiment | Not measured | Not measured | No API access |
| New same-script Coral experiment | Not measured | Not measured | No unmatched paid run |
| Existing AUD-21 short coaching report | First raw bytes p50 1.029 s / p90 1.277 s | Full audio p50 2.149 s / p90 4.747 s | Historical local report, different scripts |
| Existing AUD-21 long coaching report | First raw bytes p50 1.011 s / p90 1.183 s | Full audio p50 3.087 s / p90 4.398 s | Historical local report, different scripts |

[AUD-21 local report](AUD-21-TTS-FEASIBILITY.md) describes 30 trials/workload at
Coral speed 0.9. Its artifacts and numbers were not rerun here. They are context,
not a head-to-head result or iPhone measurement. Audli currently buffers complete
audio, sanitizes it and creates a full Blob before playback; faster provider bytes
alone cannot establish a faster learner experience.

## Licensing, continuity and usage risks

Paid-plan non-beta output can be commercially used subject to rights, applicable
terms and service-specific conditions; qualifying generated output retains usage
rights after cancellation. Free-plan output is not commercial and noncommercial
publication requires attribution. A later upgrade does not make old free output
commercial. Beta output is excluded from commercial/production use. Check the
owner's API/PAYG contract rather than assume a payment automatically satisfies all
voice-specific rights.
[Official commercial-use explanation](https://help.elevenlabs.io/hc/en-us/articles/13313564601361-Can-I-publish-the-content-I-generate-on-the-platform).

Community voices can be withdrawn: no notice period can mean immediate loss;
configured periods range from 30 days to two years. Check `disable_at_unix` and
save approved voices to the account before relying on any notice entitlement.
[Notice policy](https://help.elevenlabs.io/hc/en-us/articles/33533163659921-What-is-a-notice-period).
The [Voice Library Addendum](https://elevenlabs.io/vla) distinguishes retained
generated outputs from future model access and notes that live moderation may
add latency. Record the actual notice terms per candidate; they are unknown here.
Do not choose celebrity/Iconic licensing, imitation or cloning for this pilot.
Treat a permanent brand voice as needing explicit continuity terms, not just a
stable ID. Legacy IDs have historically redirected to different sounds:
[Official voice-change policy](https://help.elevenlabs.io/hc/en-us/articles/20939463028881-How-are-voices-updated-changed).

The current OpenAI deprecation page lists the GPT-4o mini TTS snapshots for
shutdown on **6 January 2027**. Keep today's Coral fallback now, but plan its
continuity as separate work before that deadline; do not silently substitute a
different model in this research.
[Official deprecations](https://developers.openai.com/api/docs/deprecations).

## Historical provider/fallback architecture proposal

Extract a narrow speech synthesizer interface from the existing combined provider
in a later dedicated implementation. Keep generation, evaluation and transcription
on their existing providers. Resolve server-owned `tutor_voice` independently from
`passage_voice`; the application still chooses topic, evidence progression and
one primary difficulty variable. An accent preference is presentation metadata,
never a score multiplier or availability/difficulty filter. Numeric speaking rate
remains owned by existing adaptation rules; do not add an accent difficulty axis.

Future synthesis request: complete already-approved English text, role, rate and
server-selected voice/model. Return complete validated audio with provider metadata.
Keep ownership, transcript gates, validation, sanitization and completed-assessment
durability outside the adapter. Extend cache identity with provider, model, voice,
settings version, exact text hash and rate, retaining learner/checkpoint ownership.
No keys or arbitrary voice IDs from the browser.

On retryable ElevenLabs failure before playback, allow one bounded Coral fallback
within the existing deadline; prevent retry multiplication between SDK and app.
Never stitch a partial ElevenLabs message to a different Coral voice. If both fail,
reuse the existing accessible text/replay recovery without undoing assessment.
Do not label a Coral fallback as a verified Irish/Scottish/Indian passage: report
the requested accent unavailable and use a clearly identified default passage,
without blocking the topic or changing difficulty. Rate limits and circuit breakers
can prevent repeated failures/costs; no speculative background regeneration.
Keep learner voice recordings out of this provider and research logs. Later text
egress needs minimal approved text, retention controls and appropriate account terms.

## Historical owner review and smallest next action

Review the five tutor candidates in the local blind package and six accent
discovery mappings. Permissions now succeed and no credential action is needed.
Confirm the account's commercial
and non-beta entitlement if any output will later ship; do not subscribe or enable
recurring overages just to run this report. Resolve voice IDs and any price multipliers.
Rate the completed pilot blindly and only fund repetition for finalists.

Completed sample location: `data/voice-evaluation/aud25-resolved/blind/`.
The **capped local synthesis runner** completed 22 samples; owner listening
review is pending. Production adapter and full
accent selection remain separate, unapproved implementation work. AUD-25 should
remain In Progress pending actual audition, cost/latency evidence and owner choice.
