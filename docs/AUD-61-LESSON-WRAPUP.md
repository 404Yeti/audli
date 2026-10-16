# AUD-61 / AUD-59 — conversational lesson wrap-up

Production feedback confirmed real onboarding, Indian passage accent, a complete
lesson and a real YouTube link. This follow-up removes the immediate repeated
topic question and makes optional homework part of the existing lesson session.
No production configuration or data was changed during development.

## Integration

- New lesson checkpoints use saved planner/onboarding interests automatically.
  A returning learner hears the grounded recap without a routine topic question.
  Explicit topic requests in the welcome response still update the selected
  topic. Accent discovery, consent, routing and the default tutor voice remain
  separate and retain their existing safeguards. Already-open legacy topic
  checkpoints keep their response-taking behavior on resume.
- The existing planner's distinct-completed-ID accounting and migration004
  activation baseline decide each three-completion milestone. An owned,
  transactionally locked claim records an offered milestone before speaking.
  Only the fresh completed-lesson path claims; reload/resume/duplicate delivery
  cannot automatically ask it again. Unanswered/recovery feedback remains
  accessible explicitly in Plan. Voice uses existing planner STT; typed input is
  optional preference feedback, never assessment/adaptation evidence.
- Completion/assessment persistence and closing audio finish first. The wrap-up
  reuses SessionShell, the lesson's audio element and useVoiceLifecycle. The
  optional topic check-in precedes the optional homework invitation, avoiding
  competing audio/recorders. Restored completion remains silent.
- Homework's typed route shares the conservative spoken intent parser and the
  existing owned decision/reservation/discovery transaction. No arbitrary URLs,
  new provider, extra search or quota bypass. Typed input explicitly interrupts
  recording before submitting. Ambiguous replies do not discover.
- The lower session area contains one compact reply form (Speak/Send/replay),
  then verified content/link inline. Detailed results are in a collapsed summary;
  self-reported listening/status actions remain in Plan. Assignment confirmation
  and refusal/unavailable farewell use the existing tutor speech service; their
  short transition points to the next class, with no video-comprehension claim.
- Saved assignments/lesson Review and the next three lessons remain independent
  of audio/network/discovery failure. Retry/reload controls restore saved state;
  explicit replay never automatically reassigns a video. Disabled/unknown
  homework capability still mounts no homework loader or speech effects.

## Persistence and deployment boundary

No database migration; schema005 is unchanged. New optional fields live in existing
versioned lesson and roadmap JSON: `topic_review_due` (legacy defaulttrue) and
`offered_checkins`. They preserve old snapshots on upgrade. Older strict binaries
do not understand newly written fields: do not blindly roll back to44aaaadc after
this release writes them. Functional recovery should keep a compatible follow-up
binary and disable homework. A source rollback would first need a tested JSON
compatibility bridge; deleting learner history is not a recovery procedure.

Only the new feature branch is added to Vercel preview suppression in web/site;
existing branch rules/main/CI are unchanged. This PR is for review only; no merge,
deployment, secret or production flag change is authorized by this task.

## Verification

External speech, YouTube and authentication are mocked in automated tests. Passing
fixtures establish sequencing/ownership, not live voice naturalness or evaluator
accuracy. The owner will verify the reviewed implementation on a real device only
after separately approving deployment.

Focused results during implementation:

-83 homework/planner/wrap-up backend tests passed;73 lifecycle tests passed.
-27 existing homework browser cases passed, including disabled/unknown/failed
  capability, no duplicate discovery, spoken refusal/failure, polling/reload and
  interrupted audio. Six new wrap-up cases passed at320/390/1440px, including
  sequential topic check-in and typed interruption of recording.
- Frontend unit suites, ESLint/Oxlint, lint-coverage tests, TypeScript and production
  build passed. Final all-eight hosted CI results are recorded in the PR.
- The broader backend suite required updating old daily-topic assertions; legacy
  consent-cancellation checks remain in place. Final full-suite results are
  recorded in the PR, not inferred from the focused checks above.

Screenshots (synthetic API/audio fixtures):

| Width | Invitation inline | Saved assignment inline |
| --- | --- | --- |
|320px|[Offer](screenshots/AUD-61-wrapup-offer-320.png)|[Assignment](screenshots/AUD-61-wrapup-assigned-320.png)|
|390px|[Offer](screenshots/AUD-61-wrapup-offer-390.png)|[Assignment](screenshots/AUD-61-wrapup-assigned-390.png)|
|Desktop|[Offer](screenshots/AUD-61-wrapup-offer-1440.png)|[Assignment](screenshots/AUD-61-wrapup-assigned-1440.png)|

## Owner acceptance after reviewed deployment

1. Finish typed/spoken onboarding with interests; request Indian passage accent.
   Start the first lesson: no repeated topic selection; selected passage voice
   and ordinary comprehension/transcript gates remain intact.
2. Complete three new lessons: hear one brief topic-preference check-in; keep or
   change topics by voice/text. Reload/resume/retry without another automatic
   check-in. Confirm the same cadence at six completed lessons.
3. After the saved closing, accept/refuse homework by voice and text; try “yes,
   about science.” Hear the tutor invitation/confirmation/farewell without overlap.
4. Inspect the real inline link and Plan assignment; open the video; verify the
   completed lesson in Review and three upcoming lessons remain intact.
5. Interrupt recording/playback, deny microphone, disconnect/reload during discovery
   and retry. Expect bounded recovery and no duplicate assignment/search/prompt.
   Check320/390px, desktop, keyboard focus and a real screen reader.
