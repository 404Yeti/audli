# AUD-33: Optional typed preferred name

The spoken identity question now offers an optional accessible name field in the existing onboarding interface. Voice-only onboarding remains available. Uncertain name recognition suggests typing and offers an explicit voice retry. The field restores with a pending name question and disappears after saving it, during the welcome, language clarification, other onboarding questions, and lessons.

`POST /api/onboarding/name` validates a bounded Unicode name, normalizes NFC and whitespace, and rejects empty names, control characters, markup, and malformed values. It uses existing authenticated request ownership and `save_onboarding` transactions. It updates preferences/checkpoint only, preserving lesson and learner progress. An unanswered language question remains a spoken clarification.

The checkpoint JSON contains an optional name-question revision token and a typed-name flag. The token survives identity transcription/extraction so typed submission wins either arrival order. It closes when the learner submits an answer to the next question. Duplicate submission with the same normalized name returns the saved checkpoint without incrementing its revision. Late voice requests fail existing revision checks; later language extraction cannot overwrite the authoritative name. No database migration, secret, provider configuration, or dependency change is required.

Existing saved names skip the name question, including profile review/revision. Unsaved input remains available after a request failure; ending a submission cancels its client continuation and permits retry. Account changes use the existing account-bound cancellation and ownership checks.

Verification uses synthetic providers and browser fixtures. It establishes application behavior, not real speech-recognition accuracy. PostgreSQL integration requires the existing optional test database configuration. A local webpack Playwright configuration was used because Turbopack cannot traverse a dependency symlink outside the isolated worktree; production builds already use webpack.

Release the backend before or together with the frontend. Once new checkpoints are written, a backend rollback must retain support for the two new checkpoint fields: older strict schemas reject unknown JSON fields. Deployment remains a manual approval/action; no deployment or paid provider calls were performed for this change.

Verification results: full backend regression 706 passed / 4 PostgreSQL-only skipped; final focused onboarding, typed-name and authentication regression 91 passed; frontend unit tests passed; authentication/onboarding browser suite 46 passed at mobile viewport (with existing desktop visual checks). ESLint, TypeScript and production build passed.
