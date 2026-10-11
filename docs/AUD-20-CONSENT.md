# AUD-20 learner consent

Saved `AccentPreferences` record interest, independently of lesson-local
`accent_consent`. Neither a preference, difficulty inference, nor legacy
`passage_accent` authorizes voice routing. Existing lesson JSON defaults to no
grant. No schema migration or production configuration change is needed.

The grant authorizes **one next passage**, not an entire lesson. Its minimum audit
fields are state, accent, accepted timestamp, and next exercise ordinal; lesson
ownership supplies identity and session scope. Existing check-in transcripts
already hold the spoken response; no additional audio or transcript is retained.

| Current state / event | Result |
| --- | --- |
| No grant; saved preference | Occasionally offer a supported accent; remain ordinary practice |
| Offer; clear acceptance | Accept a one-passage grant |
| Independent explicit practice request | Accept only a supported requested accent |
| Preference discovery | Save preference independently; ordinary practice |
| Decline, ambiguity, unrelated reply | No accent grant; consume that session's offer |
| Accepted; spoken cancellation or ambiguous confirmation | Revoke grant; ordinary passage |
| Accepted; explicit supported alternate | Replace grant for that next passage |
| Unsupported request | Explain unavailability; ordinary passage, never substitute another accent |
| Pause/end, ten minutes elapsed, wrong ordinal, invalid/missing timestamp | Grant cannot authorize new passage |
| Exercise saved | Atomically consume grant; save actual voice with the exercise |
| Next exercise / session | No surviving authorization |
| Replay / Retry of saved exercise | Reuse the previously authorized audio and voice |

Offer acceptance in WELCOME uses the existing REVIEW spoken turn before the
passage, including for first-time learners: “Use [accent] for the next passage?
You can change your mind and say no.” Clear confirmation continues; uncertainty
or changing the subject cancels. Explicit practice requests during REVIEW are
honored directly. Returning learners still receive their normal topic review
after the consent turn; topic replies neither grant nor extend consent. There are no new screens or accent controls. Clear refusals
never trigger another offer during that lesson; a later independent request is
still allowed. Discovery is separate, at most twice with three starts between
questions, and a discovery refusal persists the existing opt-out.

The service checks grant scope and time before TTS. The repository checks again
inside the owned exercise transaction before saving accent-specific audio; a
provider call that outlives the grant is replaced with ordinary audio before
saving. If the deadline passes inside the final transaction, saving is rejected
and Retry generates ordinary practice. Revision checks also reject cancellation during provider work. Existing
saved audio is immutable, preserving Replay/Retry and transcript gates. A missing
voice route or provider failure uses the ordinary passage voice and its existing
spoken fallback explanation. Tutor speech continues through the unchanged tutor
provider and voice configuration.

Tests use explicitly marked routing fixtures, not approved release voices. Human
validation and release gates remain unchanged; see AUD-20-PILOT-READINESS.md.

Rollback should disable the passage mapping on the compatible application build.
Older strict lesson schemas do not recognize the new consent fields; do not roll
back the binary over newly saved JSON without a tested compatibility migration.
No migration or rollback has been performed.

Release review must include saved-interest/no-consent, explicit acceptance and
refusal, ambiguous replies, supported and unsupported requests, expiry, verbal
cancellation, session end, and immutable Replay/Retry. Test legacy accounts and
cross-account requests. Complete mandatory human ratings before preparing any
voice mapping. Then verify a formally approved voice through the actual spoken
flow on isolated staging before requesting deployment authorization. Keep the
existing custom tutor ID and use mapping removal as the controlled rollback.

## Verification on 2026-10-11

- Full backend: **555 passed** (137.76 seconds). Includes 32 new consent cases,
  existing accent routing/replay/fallback cases, authentication and learner
  isolation, transcript gates, uncertainty, assessment, progress and lifecycle.
- Browser suite: **90 passed**, one integrated-media test excluded from this
  default configuration. The new first-time and returning-learner tests confirm spoken consent
  confirmation occurs before generation and normal topic review is preserved.
- Integrated native microphone/playback test: **1 passed** (4.6 minutes),
  using the real local backend and offline prescribed speech/input fixtures.
- Frontend unit suites, ESLint, TypeScript and production build: passed.
- Production npm dependency audit: zero vulnerabilities. `pip check`: no broken
  requirements; this is dependency consistency, not a Python vulnerability audit.
- SQLite and mocked authentication/provider fixtures were used. No live Supabase
  identity check or dedicated PostgreSQL contract cluster was run in this task.
- No live accent synthesis, new samples, fabricated ratings, or production edits.
  Qualitative approval remains recorded; mandatory CSV ratings and live approved
  accent end-to-end verification still block global release readiness. Existing development
  dependency advisory follow-up remains AUD-28.


Fleet admission also revalidates persisted, owned one-passage consent under the
shared PostgreSQL reservation transaction before any accent provider dispatch.
Preference persistence never creates a reservation. Shared feature off, unavailable
control state, budget/concurrency denial or a previously paid scope routes to ordinary
listening. Existing owned audio remains the authority for Replay/Retry. Operational
reservation states are distinct from learner consent; see PERSISTENCE.md migration
003 and AUD-20-GLOBAL-RELEASE-READINESS.md for disable/in-flight/lease semantics.
