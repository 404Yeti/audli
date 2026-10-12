# AUD-35 — Australian passage availability

Investigation baseline: production `be53624c968a2ef401f9a4ca172c292f56e19541`.
Related: AUD-20 and AUD-32. No deployment, paid synthesis, preference/progress
write, budget change, or voice substitution is authorized/performed for this work.

## Confirmed failure

The passage routing function `app/accent_practice.py::passage_audio` only admitted
speech rates exactly `0.75` or `1.0`. Those are the historical audition sample
rates, whereas ordinary adaptation changes speech rate in increments of `0.05`.
At `0.8`, the function skipped both reservation and ElevenLabs dispatch and
generated ordinary OpenAI passage audio. The introduction interpreted the
generic persisted fallback flag as “That accent voice is unavailable right now.”

Read-only production metadata from the post-AUD-32 release window establishes:

- One Australian fallback passage at rate `0.8`; default/OpenAI/Coral routing,
  with owned nonempty audio persisted. No scripts, transcripts or audio retrieved.
- Persisted Australian consent is consumed, and this passage was generated
  after acceptance and within its ten-minute deadline.
- Zero accent reservation rows and no active leases; zero charged characters.
- Durable fleet feature enabled, daily ceiling 5,000 characters, concurrency ceiling 1,
  unchanged control revision 3. Budget exhaustion/concurrency did not cause this.
- No accent passage/control log events in that window: the old rate exclusion
  returned silently, before the existing limit/provider outcome logs.

The real upload/answer/exercise checkpoint regression at `0.8` fails on the
unchanged baseline: consent is accepted, but saved routing is default rather
than Australian. The same test passes with the eligibility correction.

**ElevenLabs passage synthesis was not called for this failure.** This is a
routing eligibility bug plus misleading fallback wording, not evidence of
ElevenLabs rejection, a missing voice, lost consent, or AUD-32 intent failure.
Tutor ElevenLabs calls are separate; the absence of passage calls does not imply
the tutor provider was never called.

## Configuration and provider access checks

The production image's unchanged approval evidence validates Australian Clara
`tyepWYJJwJM9TTFIg5U7`, British Daniel and Irish Jessica. Successful startup with
production accent enablement requires the configured mappings to match that
evidence and requires a nonempty ElevenLabs key. Accepted Australian consent
also requires the enabled runtime mapping/control check. No configuration repair
or new approval mapping is indicated by this failure.

Non-paid `GET /v1/voices/tyepWYJJwJM9TTFIg5U7` and `GET /v1/models`, using the
existing private Audli provider credential file, returned 200: Clara's ID matches,
voice verification is not required, and Flash v2.5 supports text-to-speech.
Metadata visibility does not establish synthesis/key TTS permissions or prove
that Render's current key is identical to the private credential file. Render's
raw environment values were not retrieved; no secrets or provider bodies logged.
Current paid synthesis access and actual playback remain unverified.

## Smallest correction

- Keep the exact adaptive speech rate for ElevenLabs' documented `0.7–1.2`
  supported range, rather than whitelisting audition samples. Audli's current
  upper difficulty bound is `1.15`. Rates `0.6` and `0.65` retain ordinary fallback;
  no rate is clamped and no learner difficulty/progress changes to fit a voice.
- Preserve the original voice ID, Flash model, source scripts, human voice
  approval/hash checks, admission transactions, deadlines, scope deduplication,
  character/concurrency ceilings, kill switch and consumption behavior.
- Log fixed eligibility fallback categories for disabled practice, missing
  mapping/credentials and unsupported rate. No learner IDs, provider bodies,
  transcripts, recordings or credentials are added to logs.
- Describe the actual saved recording: “I couldn’t use the requested accent for
  this recording, so it uses our usual passage voice.” This stays accurate for
  budget exhaustion, temporary provider failure, rejection and unsupported rate
  without incorrectly declaring Clara unavailable. Existing stored passages and
  audio are not regenerated or relabeled as Australian.

The tutor voice remains `yKYzqEa22xh5PdidhN70`. Coral is the existing normal
**passage** fallback, not a replacement for the tutor. AUD-32 interpretation,
authenticated ownership, revision checks and all persistence schemas are unchanged.

Provider references (read 2026-10-12):
[speed control](https://elevenlabs.io/docs/help-center/product/core-capabilities/text-to-speech/can-i-change-the-pace-of-the-voice),
[speech endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/convert),
[voice metadata](https://elevenlabs.io/docs/api-reference/voices/get).
Historical human auditions cover `0.75` and `1.0`; synthetic routing tests at
other rates establish plumbing, not subjective accent quality or real synthesis.

## Verification and release

Focused scenarios cover adaptive rates `0.7–1.15` in `0.05` increments, the
production `0.8` case, consent reconstruction, one-passage consumption, next
ordinary passage, preference/progress preservation, unchanged tutor configuration,
disabled deployment, global kill switch, missing/invalid mapping or key,
budget/concurrency denial, unsupported rates, 403 rejection, 503 failure, timeout,
accurate introductions, persistence/replay and no extra provider calls on replay.
Browser regression exercises accepted practice followed by the actual fallback
introduction and successful lesson completion. Provider responses are synthetic.

Verification completed so far:

- Final full backend rerun: **808 passed, 4 skipped**. The skips are SQLite
  variants of PostgreSQL-only row-lock/pooler and schema bridge checks; their
  PostgreSQL variants passed. No skipped PostgreSQL case is claimed as passing.

- PostgreSQL selection: **260 passed, 1 SQLite variant skipped, 285 deselected**,
  including database-only row-lock/pooler limits and schema bridge compatibility.
- Additional authenticated `0.8` request, relogin and audio ownership scenario:
  **2 passed** (SQLite and PostgreSQL). Total PostgreSQL cases passing: **261**.
- Chromium authenticated/structured lesson checks: **73 passed**.
- Frontend unit tests, ESLint, production build and TypeScript check: passed.
- The first full backend run had one obsolete regression expecting rate `0.8`
  to be excluded. It is updated to exercise unsupported rate `0.65`, retaining
  the no-dispatch assertion. The final full rerun passed.
- `git diff --check`: passed. No migration, dependency or product UI change.

Required remediation is deploying this reviewed code correction. No production
configuration changes, migration, credit/budget increase or voice switch is needed
to correct the observed skip. Future provider rejection needs its own evidence;
do not infer it from this skipped call or silently retry another voice.

AUD-35 remains In Review after local verification. Real production Clara playback
at adaptive rate `0.8`, current production-key synthesis authorization, and actual
provider fallback remain explicitly unverified. Owner-authorized paid/manual
verification after a separately approved deployment is still required.
