# AUD-24 — Spoken welcome and accent preferences

## Review state

Local implementation on `feat/spoken-welcome-accent-preferences`, isolated worktree
`/tmp/audli-spoken-welcome`, based on remote `main` at `ffde8006c43840669aaed9959d7e1dba40fc0fa7`.
Reviewed for publication as a dedicated AUD-24 pull request. No merge, deployment,
production migration or configuration change.
Research PR #5 and the original workspace/research files are untouched.
[Tracking issue AUD-24](https://linear.app/failed-training/issue/AUD-24/add-spoken-welcome-and-remembered-accent-preferences)
remains In Progress for review and hosted acceptance.

## Foundation audit

- GitHub confirms [AUD-15 PR #6](https://github.com/404Yeti/audli/pull/6) was merged
  into main on 2026-10-09 at 09:52:57 UTC, at the base revision above. Local main
  was stale; the isolated feature uses freshly fetched origin/main.
- Original worktree: `feat/beta-feedback-forms` at `2467176`, with untracked AUD-21
  experiments, benchmark scripts, tests and research reports. Existing separate
  worktrees: AUD-15 clarification and AUD-21 research. None were changed.
- Onboarding already has authenticated ownership, transactional revision checks,
  confirmed/reliable recognition, partial-field persistence, fresh focused
  clarification, two automatic clarifications followed by a durable pause,
  explicit retry, and persisted pending recognition/resume. These are reused.
- Profile preferences and onboarding live in the existing learner profile JSON
  snapshot alongside relational projections. Scores and difficulty remain separate.
- The lesson lifecycle already asks one social check-in, reviews actual completed
  work on returning lessons, asks one topic reflection, and acknowledges it before
  the existing adaptive exercises. Those phases and questions remain.
- The current UI has one real status indicator at a time: `Audli is speaking`,
  `Listening to you`, or `Thinking`. The explanation quotes those exact labels.
  No simulated state, new pill or visual redesign was introduced.
- Linear roadmap reviewed: AUD-15, AUD-20, AUD-21, AUD-22, AUD-23 and related flow
  issues. AUD-20 remains the broader future adaptive-accent feature; AUD-24 only
  records learner preferences and adds honest conversational reflection.
- AUD-15 already appeared Done when this task began. Its latest comments still
  explicitly say hosted acceptance has not been performed. No status was changed.
- **Render deployment revision unverified:** the connected Render MCP requires
  workspace selection; it listed `robert's workspace` and a confirmation request
  was sent. No workspace is assumed. A live deploy alone would not establish the
  hosted checks below.

## Exact first-use dialogue and sequence

The existing **Let’s talk** action begins onboarding. After that one existing
user gesture, all the speech and recording turns progress automatically. There
is no added tutorial screen or separate Continue action. This preserves playback
permission and existing End/Resume behavior.

1. Introduction, before any personal question:

   > Hey! I’m Audli, your listening buddy. I’m here to help you understand spoken
   > English better, whether you’re watching movies, talking to people, or hearing
   > different accents. We’ll practice listening together, and I’ll help you
   > improve as we go.

2. Accessible status explanation:

   > My status bubbles say ‘Audli is speaking’, ‘Listening to you’, or ‘Thinking’.
   > You don’t need to watch them: I’ll finish speaking before I listen. When I’m
   > listening, that’s your turn to talk!

3. Existing identity question:

   > Before we start, what should I call you, and what language do you want to
   > train? Audli currently trains English listening.

4. Existing needs question:

   > Why are you learning English, and what do you most want to understand
   > better? Tell me about situations where listening is hardest or most
   > important for you.

5. Existing interests question:

   > What topics do you enjoy listening to? A few interests are enough, or say
   > you have no preference.

6. Optional accent preference:

   > Are there any English accents you find tricky? Maybe British, American,
   > Australian, Scottish, Indian, or something else? It’s okay if you’re not
   > sure, or you can say skip.

   An incomplete response such as “yes” requests fresh speech:

   > Which English accent would you like me to remember? You can say more than
   > one, no preference, or skip.

7. Existing saved-profile review:

   > Your listening preferences are saved. You're ready to train your ears.
   > Your first listening exercise will help us find your starting point.

8. Existing authenticated session-ready Home and first-lesson action.

Each welcome step is acknowledged through `/api/onboarding/heard` only after
playback ends. Audio generation alone does not mark it heard. Refresh/End during
speech resumes that unfinished step; completed welcome steps stay completed.
The backend rejects recording while a welcome step is active. Browser checkpoint
reads discard superseded audio before playback and restore progress after speech;
recordings are tied to their revision and discarded if the checkpoint advances. Existing returning
or partially onboarded learners do not replay the introduction. Legacy review
checkpoints can finish without being sent back through a new accent question.
Saved accent preferences are not requested again during explicit profile revision.

## Profile contract and migrations

New additive model defaults:

```json
{
  "accent_preferences": {
    "version": 1,
    "source": "learner_stated",
    "response": "Scottish and fast Australian English",
    "accents": ["australian", "scottish"],
    "status": "preferred"
  },
  "onboarding": {
    "welcome": null,
    "accents_asked": true
  }
}
```

- `response`: exact confirmed learner wording, trimmed at the boundaries,
  at most 8,000 characters. Retains unknown varieties and speech-rate context.
- `accents`: bounded canonical IDs recognized deterministically: british,
  american, australian, scottish, indian, irish, canadian, new_zealand, welsh,
  south_african. Unknown wording is retained in `response`, without invented IDs.
- `status`: unspecified for old records; preferred for stated interests;
  no_preference for none/not sure/skip or explicit decline.
- These are **learner statements, not measured weaknesses**. No performance,
  proficiency, accent score or difficulty dimension is inferred.
- Onboarding adds the `accents` stage and a focused accent clarification.
- Lesson snapshots add optional `suggested_accent` for persisted repeat suppression.
- Existing `learner_profiles.data` and `lesson_lifecycles.data` store these fields.
  No SQL migration, new column, schema-version change, dependency, Auth change or
  production operation is required. Migration 001/002 files remain unchanged.
- Writes remain account-scoped, transactional and revision checked. Lesson topic
  updates, accent updates and the checkpoint commit together while preserving
  learning state. Unreliable recognition cannot update preferences.
- Old records load defaults without backfill. Rolling back to older strict model
  code after writing new JSON keys needs a compatible reader or coordinated JSON
  cleanup; do not assume an older binary can read the extended snapshots.

## Returning lessons and capability limits

The named social greeting stays unchanged. When an actual previous lesson review
exists, every other completed-lesson return can mention a recognized preference:

> [Existing previous-session review.] You mentioned Australian English; we can
> keep that preference in mind, though accent-specific recordings aren’t available
> yet. What would you enjoy listening to more of today?

There is still one review/reflection question. Recognized labels rotate; the most
recent offered label is excluded. A sole preference is offered once until the
preference changes to another label. First lessons, unknown-only preferences,
unspecified preferences and no-preference learners receive no accent reminder.
Resume uses the existing lesson snapshot rather than choosing a fresh reminder.

Spontaneous requests during the welcome or reflection can replace the preference;
explicit declines clear it. A request such as “fast Indian English” persists the
full wording without changing speech rate. Unknown requests containing “accent”
are also retained. Nationality topics such as Indian food or Australian wildlife are not accent
requests. Accent-only replies bypass topic extraction and retain current interests.
Mixed topic/accent replies use the existing extractor; if it fails, the accent is
saved and the lesson continues with existing topics. Accent declines cannot
implicitly clear topic preferences.

For example, an Australian request receives:

> I’ll remember your interest in Australian English. Accent-specific recordings
> aren’t available yet, so today’s audio will use our usual voice.

The application owns that statement, so generated social text cannot promise a
requested voice. Original free-form accent text is never read back as instructions,
and `accent_preferences` is excluded from exercise-generation input. Current TTS
provider, voice, pronunciation, comprehension scoring, deterministic adaptation,
1.8-second silence threshold, transcript gates and saved exercise state stay intact.

**No authentic accent-generation capability is implemented or verified here.**
AUD-20 should separately verify provider/voice variety, provenance and listener
acceptance before exposing accent-specific exercise choices.

Proposed future audio contract (documentation only, not an active selection):

```json
{
  "requested_accent": "australian",
  "capability": "unsupported",
  "verified_accent": null,
  "voice_id": null,
  "verification_version": null,
  "fallback": "current_voice"
}
```

An implemented feature should only set `verified_accent` after a verified provider
capability and voice selection; record requested versus rendered variety separately
on exercise audio metadata. Never derive measured accent proficiency from a profile
preference. Keep that future contract independent of the current scoring policy.

## Verification

Final verified checks:

- Full backend: **453 passed** (SQLite), final rerun after all application changes.
- Combined SQLite/PostgreSQL onboarding, accents, lessons, auth and persistence:
  **323 passed** on disposable PostgreSQL 18.6. Final accent/account/legacy
  regressions: **54 passed** across both backends. Server stopped after verification.
- Frontend helper tests: **51 passed**.
- Full Chromium suite: **92 passed, 1 existing live-backend test skipped**.
- Lint, standalone TypeScript typecheck and production webpack build: **passed**.
- `git diff --check`: **passed**. Generated Next type references restored.
- SQL migrations 001 and 002 are unchanged; no dependency or configuration change.
- Dependencies were reused through local, untracked `.venv` and `web/node_modules`
  symlinks. These are verification helpers and must not be staged or committed.

Sandbox execution stalled in the existing audio/thread path and could not start
the browser server; final complete backend/browser checks ran outside that sandbox.
The initial PostgreSQL URI and non-UUID test fixture were corrected before the
passing combined run. No failed run is presented as a passing verification.

Tests use synthetic provider/STT/auth/media fixtures, except local PostgreSQL is real. They establish
plumbing and invariants, not actual evaluator, STT or accent fidelity.

Coverage includes welcome sequencing and durable heard steps; old-record bypass;
refresh/End/resume and account-switch cancellation; actual status labels with no
recording during explanations; single/multiple/unknown accents and rate context;
optional no preference; bounded fresh clarification and uncertainty; account
isolation and persistence; returning review with one existing question; rotation;
unsupported requests and generation-input separation. Existing full suites retain
adaptation boundaries, evidence uncertainty, audio validation and transcript gates.

## Review findings resolved

- Nationality words in topics could previously trigger an accent update. Request
  detection now requires speech context or an accent-only reply; regressions cover
  food, wildlife, history, movies and English documentaries about Indian food.
- Accent-only replies could clear topic preferences or depend on provider extraction.
  They now bypass that extraction. Mixed requests preserve topics and lesson
  availability when extraction fails; ordinary non-accent recovery is unchanged.
- Tentative wording such as “not sure, maybe Scottish” previously discarded the
  named preference. Named varieties are now retained unless the learner opts out.
- Delayed audio bodies could replay a step completed elsewhere. Authoritative
  reads before playback and after speech discard obsolete audio/checkpoints;
  captured audio is revision-bound and checked before upload. Added browser
  regressions cover superseded bodies and progress changes during playback.
- Added authenticated lesson preference isolation and actual pre-feature serialized
  JSON compatibility regressions. TTS settings and SQL migration files are unchanged.
- Completed welcome steps never replay automatically. Interrupted unfinished speech
  resumes from the beginning of that step; explicit Replay remains available.

## Hosted acceptance — still required

Record hosted URL, deployed commit, date, browser/device, and pass/fail evidence.
Do not record credentials, raw learner audio or unnecessary learner details.

- [ ] Confirm Render’s live deployment commit includes PR #6; verify health.
- [ ] Confirm the matching hosted frontend revision and authenticated routing.
- [ ] New account signup/email confirmation and private first-use onboarding.
- [ ] Real-provider spoken introduction and status explanation before any capture;
      mobile/desktop autoplay permission, audible pacing and exact state labels.
- [ ] End during each welcome step; Resume/refresh/relogin without replaying a
      completed step or automatically resuming an explicitly ended conversation.
- [ ] Name-only answer preserves name, asks only for English, records fresh speech.
- [ ] Missing goal/situation/interests clarification; explicit no interests;
      two clarifications then durable pause and explicit retry.
- [ ] Real STT uncertainty and transient extraction/TTS failure recovery.
- [ ] Single/multiple/unknown accent statements, rate context, skip/not sure;
      incomplete accent clarification and interrupted-session recovery.
- [ ] PostgreSQL preference/checkpoint persistence after backend restart.
- [ ] Account switching/isolation across speech, recording, delayed bodies and saves.
- [ ] First lesson, assessment, bounded evidence follow-ups, feedback and completion.
- [ ] Returning learner bypasses onboarding; actual previous-session review and
      topic reflection remain; occasional accent reminders rotate.
- [ ] Spontaneous accent request/decline/change saves accurately and acknowledges
      unavailable audio without changing the current voice or speech rate.
- [ ] Transcript remains gated before assessed speech; difficulty/adaptation and
      saved exercise progress behave as before.

These include the outstanding hosted AUD-15 requirements documented in its latest
Linear comments. Deployment alone does not check any of them. Do not mark AUD-15
Done, merge or deploy as part of this task.

## Recommended next steps

Review the AUD-24 pull request and dialogue before any merge.
After separate deployment approval, run the hosted checklist with real accounts
and devices. Reconcile AUD-15’s existing Done status with the recorded hosted
acceptance evidence. Keep verified accent audio and adaptive accent measurement
in AUD-20 as a separately reviewed future feature.
