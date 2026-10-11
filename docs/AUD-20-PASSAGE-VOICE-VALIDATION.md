# AUD-20 passage-voice audition — 10 October 2026

## Outcome

Three account-accessible candidates were tested with live ElevenLabs Flash v2.5.
All 12 MP3 outputs decode, preserve their saved hashes, and have no hard clipping
in the decoded mono PCM. **None is acoustically approved yet.** Human ratings are
blank. `validated-passage-voices.json` is `{}`. No conversational offers or local
runtime voice mapping were enabled, and no production configuration was changed.
Audli's custom tutor voice (`yKYzqEa22xh5PdidhN70`) was neither synthesized nor
modified in this audition.

11 October follow-up: the user personally listened and qualitatively approved
all three candidates for pilot consideration; see
`docs/AUD-20-QUALITATIVE-APPROVAL.md`. Mandatory per-clip ratings remain blank,
so the formal validation output is still `{}`. No numerical ratings were inferred.

## Candidates and measured pacing

| Intended variety (provider claim only) | Candidate | Voice ID | Rate 0.75 WPM | Rate 1.0 WPM | Initial concern |
| --- | --- | --- | --- | --- | --- |
| Australian English | Clara — Australian Professional Female with Warmth & Trust | `tyepWYJJwJM9TTFIg5U7` | 118.5–133.1 | 181.3–193.6 | Quiet raw output; needs loudness/playback review |
| British English | Daniel — Steady Broadcaster | `onwK4e9ZLuTAKqWW03F9` | 110.3–112.6 | 151.9–161.5 | Slower pacing; human review must assess broadcaster style and naturalness |
| Irish English | Jessica Gallagher — Clear and Neutral | `DbwWo4rVEd5NrejHYUnm` | 145.6–154.8 | 203.7–210.0 | Fastest candidate; earlier provider language metadata includes both Irish and British claims |

API access and model support were checked against each exact ID. Clara and
Jessica expose base-rate sharing (1.0); Daniel has no shared premium rate. No
account/library changes or new voices were added. Provider metadata is discovery
evidence only, not proof of accent authenticity or educational suitability.

## Samples

Open `data/voice-evaluation/aud20-validation-20261010/review.html` locally. The
review player uses numbered samples; identity mapping is in results.json.
Two original English passages (coast and library) are identical across candidates.
Both are tested at 0.75 (Audli's default lesson rate) and 1.0, without accent
prompts, tags, tutoring speech, external media or learner data. Requests match
app/accent_practice.py's Flash passage endpoint and speed settings.

| Candidate | Coast 0.75 | Coast 1.0 | Library 0.75 | Library 1.0 |
| --- | --- | --- | --- | --- |
| Clara | P01.mp3 | P02.mp3 | P03.mp3 | P04.mp3 |
| Jessica | P05.mp3 | P06.mp3 | P07.mp3 | P08.mp3 |
| Daniel | P09.mp3 | P10.mp3 | P11.mp3 | P12.mp3 |

The coast passage contrasts an earlier-than-expected rainstorm with a changed
market visit. The library passage contrasts four with half past five and a
changed transport plan. Review that all facts and time contrasts remain clear.
These test scripts are available to reviewers; they are not served through
learner exercise endpoints.

Durations range from 23.3 to 49.0 seconds. Rate 0.75 produces longer audio than
1.0 in every pair; WPM uses supplied text, not a verified transcription. Peaks
range from -17.99 to -0.83 dBFS. RMS ranges from -40.13 to -17.16 dBFS. Clara's
RMS is about 17–23 dB below Jessica's. There is no loudness normalization in the
current passage adapter. This objective volume difference is a validation concern,
not an auditory judgment. Waveform analysis cannot verify correct pronunciation,
accent, naturalness, one-speaker delivery or unchanged spoken facts.

## Human validation and initial configuration

A reviewer familiar with the intended English variety should listen before
viewing identity metadata. Complete every row of ratings.csv with reviewer identity,
documented accent familiarity, the accent actually heard, 1–5 ratings for clarity,
consistency, naturalness and ESL suitability, any critical errors, and concrete
listening notes. Examine vowels and connected speech across both passages and
rates, meaning-bearing dates/times, artificial emphasis or pauses, and playback
volume on learner devices. All four clips for an accent must score at least 4 in
every category, match its target, and have explicit `critical_errors=none`.
Disagreement or missing evidence blocks that accent; metadata cannot fill gaps.

Run:

```
.venv/bin/python -m scripts.validate_passage_voices review --output data/voice-evaluation/aud20-validation-20261010
```

This writes a proposed `validated-passage-voices.json` only for fully reviewed
unchanged audio. It does **not** apply it to runtime, .env, Render or production.
The reviewer CSV and audio hashes bind the proposal to this audition. Model/rate
changes or materially different synthesis settings require renewed review.

Minimal verified configuration today is `{}`. Daniel is the first candidate to
review first in staging because its measured pace is lowest; that is a
provisional ordering, not an endorsement of accent or quality. Add Clara only if
its raw playback volume is suitable (or review a separately normalized pipeline).
Jessica needs particular attention to pace and Irish consistency. Do not lower
lesson difficulty or alter scoring to compensate for a voice's pace.

The tested rates are 0.75 and 1.0, not the entire application range of 0.6–1.15.
Endpoint errors at untested rates still fall back to the persisted default voice
and spoken explanation. No unsupported-rate clamping or new difficulty dimension
was introduced. Human/device review is the remaining activation blocker.

## Usage and cost evidence

12 synthesis requests, 5,442 submitted characters, no retries. Preflight required
active subscription, included credit headroom for the full 5,442-credit worst-case
budget, no premium multiplier, and no credit extension/top-up. The account initially
reported 92,296 / 121,314 credits used. The immediate post-run read was unchanged; a later read reported 93,496 used,
a **1,200-credit increase**, matching the aggregate cost-header units.
Nominal published self-serve Flash pricing implies **2,721 credits** (0.5 per
character); this is an estimate, not measured billing. The response `character-cost`
headers total **1,200 units**; their exact standalone billing semantics were not established. The matching
account delta supports 1,200 reported credits for this run, subject to concurrent
account usage; it does not establish a USD charge. Account usage can lag or
include concurrent activity. USD billing cannot be inferred from either reading.
No purchases, plan changes, credit extensions or overage actions were performed.

Primary pricing reference: https://help.elevenlabs.io/hc/en-us/articles/27562020846481-What-are-credits

The exclusive synthesis.started marker and pre-request ledger prevent accidental
reruns after interruption. Generation stops on any failed request and does not
silently retry another paid voice. Scripts never persist credentials or provider
error bodies. Artifacts include raw samples, measurements/hashes, metadata,
review.html, blank ratings.csv and the empty validation proposal.

## Verification

The approval tool rejects empty, incomplete, low-rated, wrong-accent, critical-error
or changed-audio evidence. Selected-passage integration tests cover all three
routes, actual voice metadata, owned saved audio, Replay/Retry reuse after mappings
change, provider fallback, unchanged learner state and transcript restrictions,
and interrupted generation rejecting a stale write. Fixtures establish application
plumbing; they do not rate accent authenticity or evaluator accuracy. Authentication
and existing lesson/conversation regression checks are also run.

Final verification: **514 backend tests passed** (117 seconds); 34 focused passage
routing/approval tests passed; git diff --check and script compilation passed.
No frontend/runtime feature code was changed in this validation phase.

Release scope is now every authenticated learner, with no account allowlist.
See AUD-20-GLOBAL-RELEASE-READINESS.md. The feature flag is disabled initially;
production startup revalidates mandatory CSV ratings and audio hashes before
allowing an approved mapping to activate. No approval or activation occurred.
