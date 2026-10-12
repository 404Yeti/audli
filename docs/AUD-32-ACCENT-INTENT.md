# AUD-32 — Accent intent interpretation

Review baseline: production `f2a36388ff8ec348c5a9a4d215e8d56d56ed71b1`.
Related work: AUD-20, AUD-29, AUD-30, AUD-31.
This change is local and must be reviewed before release. No paid provider calls.

## Reproduced cause and evidence

The deployed lesson handler made independent decisions using the practice intent
classifier, discovery response extraction, preference extraction, and a separate
suppression regex. A discovery response could therefore overwrite the meaning
of a current practice request. The practice grammar also rejected otherwise
clear requests containing `for the next passage` because its accent-only word
filter did not recognize `for`.

On the unchanged production-code baseline, a synthetic reliable transcription,
`Can we practice Australian? I don't want the usual accent.`, reproduced the
reported outcome through `/attempts` and `/answer`: `WELCOME_ACK`, saved
`no_preference`, no authorized accent, and the acknowledgment saying Audli would
not ask about accent preferences. The practice classifier handled negative
wording before the positive request; discovery extraction then interpreted
`don't want ... accent` as a persistent refusal. This is a reproduced failure
mechanism, not a claim about the owner's exact transcription.

Available production metadata was inspected read-only and in aggregate. The
post-release lesson snapshot inspected had no recorded grant. A final checkpoint
does not retain the complete opening conversation, so the precise production
utterance and sequence cannot be reconstructed from that metadata. No learner
recordings, transcripts, identifiers, credentials, or provider calls were used
for the reproduction. No evidence establishes a network interruption or
ElevenLabs failure as the cause of this intent bug.

## Authoritative interpretation and transitions

`app/accent_intent.py` returns a strict structured interpretation: intent kind,
accent target, optional persistent preference, and optional topic clause. The
lesson answer route invokes it once, after reliable recognition and before any
state mutation. Legacy helper entry points delegate to this interpreter instead
of maintaining competing classifiers.

Precedence is explicit: current practice requests first (unsupported alternatives,
uncertainty, conflicting later refusal and multiple choices cannot grant consent);
scoped future-question suppression; contextual acceptance/refusal; disclosure or
none; unresolved ambiguity; ordinary topic/check-in conversation. Suppression
requires a command about asking/suggesting/remembering accents. Negative wording
about the usual voice or another accent cannot suppress preferences. Separate
positive topic clauses cannot supply an accent target or accept an offer.

| Learner meaning | Preference transition | Passage/lesson transition |
| --- | --- | --- |
| Australian accents are difficult | Save learner-stated interest | No consent; existing AUD-29 optional offer, without repeating an offer already made in this lesson |
| Request Australian for the next passage | Preserve preferences | Accept one grant for the current new-passage ordinal and existing deadline; acknowledgment names Australian |
| Sure, let's try that | Preserve preferences | Accept only the active offer/clarification target; no target means no grant |
| No, not today | Preserve preferences for an offer | Decline current practice; declining an actual discovery question retains its existing recorded-response policy, with no suppression promise |
| Please stop asking about accents | Save existing explicit no-preference state | Expire current authorization; suppress future preference questions/offers |
| Maybe later / tentative request | Preserve preferences | At most one clarification when a target exists; otherwise defer; a second unresolved answer never grants consent |
| Let's talk about travel | Save explicitly selected topic | Never authorize or replace an accent; preserve an already accepted grant, or clarify a pending offer once |
| Unsupported accent request | Preserve preferences | No grant and an accurate unavailable/usual-voice acknowledgment; no silent substitution |

A compatible compound request may explicitly suppress future questions and
request Australian now. Both meanings are retained together: the preference
opt-out does not revoke the separately requested one-passage grant. A request
followed by a conflicting refusal requires clarification and cannot authorize
synthesis.

Discovery is not scheduled immediately after resolving/deferring a practice
conversation. Ordinary topic replies still reach AUD-30's suitable discovery
boundary. Unanswered uploads do not resolve discovery. Preference disclosure
never authorizes a passage and repeating a disclosure never restarts a declined
offer within the same lesson.

## Persistence, ownership and routing

The existing authenticated request ownership, reliable-recognition gate, revision
checks, lesson lock and transactional preference/checkpoint save remain in place.
Upload persists transcription only; answer commits interpretation, preference,
consent and its spoken acknowledgment together. A lost successful upload or
answer response can be recovered from the current checkpoint; replaying its old
revision returns 409 without overwriting a committed decision.

Existing `AccentConsent` fields and clarification flags are reused. Every
clarification checkpoint has a known target readable by the deployed reader.
There is no migration, schema version change, new UI, parallel onboarding,
provider classifier or added paid API call. Settings and tutor voice are unchanged.

Exercise generation still calls the existing server authorization, reservation,
budget, kill-switch and ElevenLabs routing code. An Australian grant targets
Clara (`tyepWYJJwJM9TTFIg5U7`) for exactly one eligible new passage. A saved recording
keeps its original audio; consent moves to the next new passage without extending
its deadline. Successful synthesis or fallback is persisted with actual passage
metadata. Introduction copy describes that outcome; replay returns the stored
audio without another synthesis. Consent is consumed after that passage, and
existing pause/deadline rules still expire unused consent.

## Verification

Final non-paid verification:

- Full backend suite: **761 passed, 4 skipped**. The four skips are the SQLite
  variants of PostgreSQL row-lock timeouts, session-pooler timeouts, startup
  bridge and schema-3 assessment compatibility; they are not claimed as passing.
- AUD-32 conversation scenarios: **160 passed** across SQLite and PostgreSQL;
  the additional previous-opt-out request scenario passed on both backends
  (**2 passed**) after the full suite completed.
- PostgreSQL persistence/ownership/Settings/fleet-control regressions:
  **164 passed, 1 skipped, 187 deselected**. The one skip was the SQLite variant
  selected because its test name contains `postgres`, not a skipped PostgreSQL case.
- Both previously skipped PostgreSQL-only control tests explicitly rerun:
  **2 passed**. Schema startup bridge and schema-3 compatibility run on PostgreSQL
  as part of a focused check: **13 passed, 2 SQLite skips, 146 deselected**.
- Frontend unit checks, ESLint, TypeScript checking and production build: passed.
- Chromium authenticated/structured lesson browser regressions: **69 passed**.
- `git diff --check`: passed. No migration or frontend source change.

Scenario tests exercise all seven meanings through real upload/answer routes
across first lessons, returning learners, active discovery, pending offers,
resumed checkpoints, topic changes, saved recordings and new passages. They
cover compound negative wording, suppression plus current requests, unsupported
choices, bounded ambiguity, conflicting requests/refusals, persistence after
repository/app reconstruction, lost upload/answer responses, Clara routing,
provider failure, accurate acknowledgments, one-passage scope and replay.

Regression coverage also includes onboarding, Settings/account isolation,
authentication, existing lesson progress, provider/fleet controls, transcript
gates and schema-3 persistence compatibility. Browser checks use mocked API and
media responses; backend scenarios establish the actual interpretation and
routing behavior. Mock audio cannot establish human-perceived accent quality.

## Release and remaining limits

Release review is pending; nothing was pushed, merged or deployed. The change is
backend-only; frontend checks verify compatibility and do not require a new UI
release. No production preferences, progress, controls or recordings were changed.

The interpreter deliberately uses bounded deterministic grammar. Untested or
unreliably recognized freeform phrasing does not become permission by inference.
Actual production speech recognition, natural spontaneous requests, Clara
playback and real provider fallback remain unverified by this non-paid suite.
The owner should manually test those spoken interactions after an approved release.
The exact original production transcription remains unavailable.
