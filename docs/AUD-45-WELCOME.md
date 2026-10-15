# AUD-45 first-time welcome

New learners see Listen, Respond and Improve before the existing spoken onboarding entry. Next, Back, swipe and labeled page indicators navigate; Skip and “Let's get started” persist a terminal choice. The existing “Let’s talk” gesture then starts the unchanged spoken welcome and conversation. The walkthrough never opens a microphone or calls a provider.

`OnboardingProgress.walkthrough` is an optional field in the existing account-owned JSON checkpoint. The revision-checked endpoint uses the existing save transaction and authentication/ownership middleware. No schema migration or new table is required. A stale tab restores authoritative state; completion is idempotent. New devices and refresh restore the saved step. Completed learners, saved profiles, active onboarding, existing attempts and explicit onboarding restart bypass the introduction.

The UI composes the existing Figma-derived Audli mascot (node 7:23), Inter, brand colors and controls. Slides focus their heading after navigation, indicators have labels and current-step semantics, targets are at least 44px, and swipe preserves vertical scrolling. Reduced motion disables slide and indicator transitions; mascot reduced-motion behavior is inherited.

Screenshots: [Listen](screenshots/AUD-45-listen-390.png), [Respond](screenshots/AUD-45-respond-390.png), [Improve](screenshots/AUD-45-improve-390.png). Additional captures cover 320px and 1440px.

Verification is recorded on the PR. Browser/media fixtures verify orchestration, not actual evaluator or microphone accuracy. No paid-provider calls, migrations, merge or deployment are part of this change. Vercel preview deployment is disabled for the review branch.

The new JSON property is additive for this application. An older strict snapshot parser would need to tolerate/remove `onboarding.walkthrough` before being used as an application rollback; no rollback or production write is performed here.
