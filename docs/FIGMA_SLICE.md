# Approved Figma lesson slice (AUD-10)

Source: https://www.figma.com/design/6AfBOpHqAHcyKvaOXBxyxC

Home is based on node `2:133`; shared components come from the Audli Components page. The file provides component variants and a 2.4-second looping voice animation, but no Home prototype reactions or flow start points. Lesson screens therefore compose the approved components around the existing server conversation phases.

## Implementation and behavior

- Next.js App Router and plain CSS remain in use. Inter is served locally through `@fontsource/inter`; `motion` renders the Figma keyframes. Exported SVGs are local files in `web/public/audli`. Motion resets when its state changes and rests when reduced motion is requested.
- Home uses the approved 326 × 342 lesson card, 54-pixel primary button, 26-pixel card radius, gradient, typography and spacing at a 390-pixel viewport. Lesson cards use safe generic copy and the configured clip duration. The public API deliberately does not expose the generated title, a CEFR level, or accent metadata before assessment, so the coffee/B1/American-English sample copy is not presented as real learner data.
- Small text uses darker teal/coral/yellow counterparts for readable contrast. Controls have keyboard focus indicators and at least 44-pixel-high targets; recording actions can stack on narrow screens rather than overlap.
- Home navigation marks Home active. Plan, Review and Settings are disabled. Navigation disappears during the lesson. The spoken-file fallback appears when microphone permission fails, preserving the normal Home geometry.
- Initial learner name/goal collection remains the existing form. Permission is requested before generating/starting the first clip; denied permission can continue using a spoken audio file, including for a new learner.
- The introduction uses the browser's English speech synthesis with replay and accessible text. Its voice can differ from the backend voice. This avoids changing backend speech contracts or prematurely starting an evidence conversation. Unsupported/blocked speech has a text fallback.
- A completed summary or follow-up audio cue starts recording once. Replaying the same cue after Cancel does not start another capture; the microphone control remains available. Autoplay failure leaves replay/manual-record controls available.
- Existing `captureRecording`, original-Blob uploads, MIME selection, final-chunk collection, object-URL cleanup and the 119-second auto-stop remain. Silence detection is intentionally absent. Cancel disposes the active capture without uploading it.
- Finish automatically transcribes. The existing recognition check remains mandatory; transcription uncertainty is visible and empty recognition cannot be confirmed. Failed uploads retain the original local Blob and preview for Retry transcription. Unsent audio still cannot survive a reload.
- Summary/follow-up progression, uncertainty, evaluation, adaptation and transcript gates remain owned by the backend. Terminal feedback reads only the server's safe terminal message; detailed numerical metrics stay in the optional details disclosure.

## Changed files

- `web/app/page.tsx`, `globals.css`, `layout.tsx`: Home, introduction and lesson UI, shared tokens, local fonts, capture controls and recovery handling.
- `web/components/lesson.tsx`, `motion-tracks.ts`: the nine requested reusable components, navigation, logo variants and exact motion tracks.
- `web/lib/session.ts`: automatic-capture eligibility, evidence-card classification and microphone preflight.
- `web/public/audli/*.svg`: 29 local Figma exports, including static logo variants, animated pieces and control/navigation icons.
- `web/package.json`, `package-lock.json`, `eslint.config.mjs`, `playwright.config.ts`: runtime assets/motion, lint and browser-test setup. Existing Next/React/TypeScript versions remain unchanged.
- `web/next.config.ts`: permit the local browser-test origin and disable generated framework agent files.
- `tests/session.test.mjs`, `web/tests/lesson.spec.ts`: focused helper and browser regression tests.
- `.gitignore`: generated browser-test reports. `docs/FIGMA_SLICE.md`: mapping, verification and manual acceptance notes.

The existing user-authored `AGENTS.md` changes were preserved.

## Backend mapping

| UI step | Existing API/state |
| --- | --- |
| Restore Home/profile/current lesson | `GET /profile`, `/exercises/current`, `/exercises/{id}/conversation` |
| Prepare next activity | `PUT /profile` for a new learner; `POST /exercises` |
| Introduce / listen | Frontend introduction; public exercise audio; `LISTENING` |
| Clip ends / Your turn | `POST /exercises/{id}/conversation/listened`; `AWAITING_SUMMARY` |
| Speak the issued cue | `POST /exercises/{id}/coach-audio` with the server cue ID |
| Record / Finish / processing | Frontend recording substates; `POST /exercises/{id}/attempts` |
| Confirm recognition / evaluate | `POST /attempts/{id}/assess` with confirmed text and issued follow-up ID |
| Insufficient evidence | Server selects at most two follow-ups; `AWAITING_FOLLOWUP` |
| Terminal feedback | Durable result plus server-composed terminal cue; `GIVING_FEEDBACK` |
| Ready / Keep going | `POST /exercises/{id}/conversation/ready`; `POST /exercises` |
| Optional transcript | Existing gated transcript endpoint, after final completion only |

No backend files or contracts changed. The `v0.1` tag is untouched.

## Verification results

- Backend: **153 passed**.
- Frontend helpers: **25 passed** (API 6, recording 11, conversation 4, session 4).
- Chromium UI tests: **10 passed**, with mocked API/media.
- TypeScript, ESLint (no errors/warnings), production build and `git diff --check`: **passed**.
- Home and recording screenshots were reviewed; all 29 SVG exports are nonempty local files. Browser checks verify the Home asset geometry, card measurements, 320-pixel overflow behavior and a full motion loop.
- Real learner/browser/provider audio acceptance remains unverified. `AUD-10` has a verification comment and remains In Progress pending acceptance and the tooling advisory below.

## Verification commands

From the repository root:

```bash
.venv/bin/pytest -q
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run build
cd web
npx playwright install chromium
npm run test:browser
```

The Playwright suite mocks API responses, browser recording, and media playback events. It verifies UI orchestration, recoveries, transcript-control visibility, capture cancellation, the 119-second limit, reduced motion, local SVG geometry, a complete animation loop, and a 320-pixel layout. It does not validate real microphone codecs, actual audio playback, STT fairness, or evaluator accuracy.

## Manual verification

1. Start FastAPI and Next.js as described in README. Use a private OpenAI configuration and separate test data; never put the key in the browser.
2. Open Home at 390 and 320 pixels. Start training, allow the microphone, hear/replay the introduction, then listen to the full clip. With reduced motion enabled, verify the mark stays still.
3. Hear Your turn. Recording should begin after the cue ends. Check the timer, Cancel, Stop and Finish; Cancel must not upload. Replay the cue after Cancel and use the manual microphone control to restart.
4. Finish a real spoken response. Confirm/correct only recognition errors. Verify the original passage and expected answers remain unavailable, including during a targeted follow-up.
5. Give both a complete summary and an incomplete summary. Confirm bounded follow-ups, distinct unknown/incorrect evidence and stable difficulty if evidence remains incomplete. Hear concise terminal feedback without scores; only then open the optional transcript.
6. Deny permission and retry or use an audio file. Try silence, failed clip playback, failed coach playback/TTS, offline upload, offline assessment and a reload after transcription. Retried requests must not duplicate follow-ups or adaptation; failed uploads must retain the local recording.
7. Keep going and verify a fresh clip uses the saved adaptation. Leave a recording open for 119 seconds to check real-browser timer/encoder behavior. Browser throttling can still overrun the backend's 120-second cap; validation must reject overlong audio safely.

## Remaining limitations

- Home cannot reproduce the sample lesson title/level/accent without adding backend-safe metadata. Current copy avoids invented learner information and hidden passage content.
- Introduction speech uses the browser voice; other coaching audio uses the existing provider. Browser/provider acceptance with real learner speech is still required.
- The installed Next.js ESLint development dependency chain reports five high audit entries deriving from the `braces` advisory. Registry latest is still `3.0.3`; the audit suggests a major downgrade of the Next lint configuration. No automatic downgrade was applied. Production dependencies are not implicated in this audit result.
