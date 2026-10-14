# AUD-40 final Plan UX polish

Baseline: deployed dbfb8f9d5c24a188dc9a9ed1c97707b462a861cd. Owner confirmed
three upcoming lessons and preserved completed history; final UX acceptance pending.
Keep AUD-40 In Review until owner confirms.

Remove learner-facing check-in progress counters, status messages and the dedicated
Listening check-in section. Keep unobtrusive Share feedback/Not now controls only
when the existing optional spoken milestone is available; spoken question text
appears only during the conversation for accessibility. Audio/replay/stop, answer/
skip persistence, scheduling, ownership and save-before-coaching remain unchanged.

Newly generated outlines alternate short updates, practical instructions and
personal stories, with distinct comprehension tasks inside the existing focus.
Contexts rotate by lesson number so each rolling refresh advances the context
actually used at lesson start. Contexts are saved in the outline topic and reach on-demand lesson generation
through the existing plan_topic path. Difficulty, scores, accent consent and
provider routing remain unchanged. Repeated reads do not regenerate older blocks;
new variety appears only after natural completion/preference/feedback regeneration.
No production plans are bulk refreshed, rewritten or reset.

Display labels map B1 to Intermediate, short duration to Short listening practice
and reduced speech rate to Slower pace. Numeric CEFR/duration/rate settings remain
unchanged and are tested for display-only mapping.

Validation: focused mocked planner/preservation tests40 passed/2 backend-specific
skips; full backend919 passed/18 skipped; focused spoken-feedback browser regression
passed. Web units, ESLint/Oxlint, lint coverage, TypeScript and build passed.
Required GitHub CI covers full PostgreSQL, exact rollback003/004, frontend, browser
and security; all eight jobs must pass before merge/deploy. No dependency, schema,
auth, onboarding, accent/voice/budget, progression or planner transaction changes.
Both projects disable previews for this branch, preserving existing exclusions.

Production verification will use public health/auth boundaries/deployment metadata
and served frontend assets, plus synthetic frontend-only data if needed. Actual
authenticated final experience remains owner acceptance. No paid AI/TTS calls.
