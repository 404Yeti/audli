# AUD-31: spoken acceptance and passage routing

Base production revision: `98a10c02f442aa6685bcebd3f696209f7e591db0`.
Related work: AUD-20, AUD-29, AUD-30.

## Production evidence

Read-only inspection of the reported lesson found an Australian offer, a saved
Australian preference, and consent state `none` with no acceptance timestamp or
passage ordinal. Its newly generated passage used OpenAI/coral with
`fallback=false`; speech rate `.75` was eligible for the existing accent pilot.
An older unfinished ordinary recording was attached before that new passage.

Sanitized Render access logs showed successful attempt and answer submissions.
There were three successful attempt submissions before the second answer; the
browser's network error does not establish that consent persistence failed.
The lesson checkpoint does not retain the original consent-answer transcript
after subsequent topic selection. Its exact wording cannot be reconstructed.
No learner content, account identity, credentials or raw audio is included here.

## Reproduction and root cause

On the deployed code, the synthetic response “Yes, I'd like to try that.” to an
Australian confirmation is classified as unknown. `resolve()` immediately
announces the usual passage voice and leaves consent ungranted. This reproduces
the observed offer/acknowledgment sequence, consistent with the persisted state.

Two related lifecycle defects were found: acceptance during WELCOME sets another
confirmation, whose unknown answer can erase an already accepted grant; attaching
an unfinished recording leaves a new-passage grant at the old exercise ordinal,
so it becomes invalid when the saved recording is completed.

## Fix

- Recognize complete natural affirmative replies with bounded deterministic
  grammar. A leading yes plus unrelated speech, preferences, multiple choices,
  uncertainty and future intent remain insufficient for authorization.
- Use existing checkpoint flags for one short spoken clarification. An unresolved
  second reply moves on safely. No schema changes or migration are needed.
- Accept once; preserve accepted legacy grants through subsequent topic answers.
  Explicit revocation, pause, deadline and one-passage consumption still apply.
- Preserve saved recording audio/progress and move the grant ordinal to the next
  new passage without extending its timestamp. Say that the saved recording comes
  first. Replay continues serving the original stored audio.
- Derive acknowledgment validity from authoritative consent. Explain ordinary
  audio when a requested accent cannot be committed, including consent expiry
  or a kill-switch race.

Changed components: `app/accent_practice.py`, `app/lesson.py`,
`app/lesson_api.py`, `app/main.py`, `app/storage/repository.py`.
Clara's approved mapping, tutor routing, authenticated ownership, CAS checkpoint
writes, shared budget controls and kill switch are unchanged.

## Verification and release

Automated provider responses and speech recognition are synthetic throughout.
Focused tests cover natural acceptance, ambiguous clarification, refusal,
legacy/resumed checkpoints, interrupted transcription, lost committed responses,
authenticated account isolation and relogin, consent persistence/consumption,
Clara routing, fallback explanation, expiry and replay. Browser tests exercise
same-phase clarification and recovery after a committed upload loses its response.

Verified locally:

- Relevant backend regression selection: **634 passed, 2 SQLite-only skips**,
  with SQLite and disposable PostgreSQL 17.11 fixtures enabled.
- Final reliability suite plus both explicitly selected PostgreSQL-only control
  tests: **44 passed**. The row-lock timeout and pooler session-option tests passed
  on PostgreSQL; they were not counted as passed in their skipped SQLite variants.
- Final acceptance/refusal and strict no-consent grammar checks: **23 passed**.
- Browser voice lifecycle/authentication/Settings selection: **69 passed**,
  including both added clarification/retry cases. A temporary Webpack dev-server
  configuration accommodated shared dependencies in the isolated worktree.
- Frontend unit suite, lint, TypeScript and production Webpack build: passed.
- `git diff --check`: passed.

Release requires review. No deployment or paid provider calls are authorized by
this investigation. Live STT classification and the acoustic result with Clara
remain unverified. The original spoken consent reply cannot be recovered from
the available checkpoint metadata.
