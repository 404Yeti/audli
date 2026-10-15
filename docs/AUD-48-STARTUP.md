# AUD-48 branded startup

Reusable `AudliLoadingScreen` composes the existing Audli wordmark, local Inter,
paper/color tokens, and approved inline `AudliMascot` (Pip). Existing `mascot-think`
keyframes gently float Pip by 2px over three seconds; system and application
reduced-motion rules disable animation. Inline SVG and an explicit aspect ratio
reserve mascot geometry without network requests. No assets, dependencies,
provider calls, database changes, or minimum splash duration were added.

Discovery: `AGENTS.md`, `docs/FIGMA_SLICE.md`, `docs/APPLICATION_FLOW.md`, existing
mascot/motion/logo components, auth and onboarding browser tests, and Linear
AUD-48. Live Figma file `6AfBOpHqAHcyKvaOXBxyxC` Components page and Idle logo
`16:2` were reviewed; the existing mascot/wordmark remain the implementation.
There is no dedicated approved startup composition in the discovered components.

Startup gates:
- Auth configuration and initial Supabase session event: splash; existing public
  landing/sign-in/recovery routes and verified account ownership remain intact.
- Profile destination read: splash until server selects onboarding or session.
- Returning learner profile, exercise, and lesson checkpoint hydration: splash
  before rendering Home or a saved completion; active lesson restoration keeps
  its existing voice behavior.
- Initial onboarding checkpoint/profile hydration: splash before rendering the
  saved AUD-45 walkthrough or spoken onboarding entry.

A 20-second deadline applies to each startup phase. Errors stop decorative motion,
announce the failure and show a keyboard-accessible Retry connection button.
Account retry reloads the initial auth flow; profile/checkpoint/onboarding retries
repeat their original reads. Timed-out operations are cancelled, and existing
operation assertions reject late responses. Background requests retain their
existing presentation. No successful account or routing state is synthesized.

Verification includes browser assertions for 320/390/768/1440px, ARIA status and
alert semantics, keyboard retry, reduced motion, deferred auth/profile/checkpoint/
onboarding reads, errors, timeout/late results, session restoration and ownership.
Screenshots and an animation video are produced by `web/tests/startup.spec.ts`.
Automated accessibility checks do not replace listening with a physical screen
reader. Owner visual review remains required before merge or deployment.
