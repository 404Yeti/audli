# AUD-62 — voice identity and adaptive classroom

This follow-up starts from production `fef4413a7f2b618b7b88a37784ef2ca84fa999d6`.
No production configuration, credentials, schema or learner records were changed.

## Investigation evidence

Read-only, aggregate observations in the post-deployment window:

- Startup selected ElevenLabs and the original Audli voice
  `yKYzqEa22xh5PdidhN70`; a key was configured. Two ElevenLabs tutor attempts
  succeeded. Eight returned HTTP401; all eight fell back successfully to OpenAI.
  This confirms provider rejection and actual fallback, rather than merely a
  perceived voice change. Existing telemetry did not retain the provider error
  code, so the exact401 cause cannot be reconstructed. It must not be described
  as a proven invalid key: [ElevenLabs documents quota and other401 causes](https://elevenlabs.io/docs/help-center/technical/api-error-code-400-or-401).
- All tutor paths already route through `TutorSpeech`: onboarding, classroom,
  acknowledgment, feedback and homework. Passage synthesis remains separate.
  A defect allowed transient fallback audio to be cached under the preferred
  voice signature, so later cues could keep replaying Coral after recovery.
- The window contains one lesson with no scheduled accent offer or consent grant,
  and two default OpenAI passages marked `fallback=false`. This indicates no
  authorized accent route for those passages, rather than a failed Indian voice
  synthesis. These aggregate records do not establish which utterance/request
  the learner intended. Saved accent preferences are interest, not one-passage
  consent; the existing cadence and explicit grant remain authoritative.
- OpenAI STT marks fewer than three recognized words as insufficient *assessment*
  evidence. Homework incorrectly applied that gate to reliable short choices
  such as “Yes please.” Its conservative parser also rejected “Yeah, sure,”
  “Maybe next time,” and an explicit “Can you find something about…” request.
- No physical microphone or Safari capture defect was established from the
  available metadata. The existing MP4 support, final-chunk assembly, silence
  detection, bounded capture and cancellation cleanup are retained.

## Changes and safety

- Version tutor cache signatures to exclude old unclassified cached audio. Static
  prompts cache only preferred-voice success. Feedback fallback remains owned
  and replayable, with explicit provenance; the next synthesis request retries
  the preferred voice. One primary attempt and one bounded genuine fallback
  remain; there are no automatic provider retry loops or live provider probes.
- Audio responses carry only provider/fallback headers. The classroom and
  onboarding show a compact “Backup voice used” disclosure when necessary;
  this records a past fallback, not a claim about the currently playing clip.
  Old-account responses cannot update the disclosure.
- Provider diagnostics retain fixed act/provider/outcome/error-code categories,
  HTTP status and timing. Error envelopes are bounded to16KiB and only allowlisted
  codes survive. No provider messages, learner speech, tokens, profile fields or
  credential values are logged. Passage diagnostics distinguish no grant,
  provider failure, admission limits and actual routing.
- A dedicated homework gate permits a recognized short choice only with adequate
  confidence and no uncertainty other than the assessment-length warning.
  Unknown confidence, genuine uncertainty, mixed decisions and ambiguous speech
  still cannot assign. Existing comprehension recognition/scoring gates are
  unchanged. Voice and typed text share the same pure intent parser.
- One brief clarification and at most one automatic spoken correction are allowed
  per mounted invitation; further uncertainty waits for explicit voice/typed
  input. Discovery starts only after a reliable choice. Existing reservations,
  quota, owned persistence, cancellation and saved completion are unchanged.
- Shared `SessionShell` modes are Focus, Interaction and Content. Layout-aware
  flex sizing, minimum heights and mascot widths transition subtly; audio stays
  in the same DOM element. Reduced motion disables transitions. Normal flow
  favors voice; typed input is a native accessible disclosure. Assignment title
  is bounded to three readable lines, duration/source and a44px external link
  remain primary, with objective/publisher/disclosures available on expansion.
- No schema or serialized domain-model changes. Schema005, global homework flag,
  five-admission UTC budget, YouTube credentials, Plan/Review and transcript gates
  are preserved. Only this feature branch is added to preview suppression.

## Screenshots

All screenshots use synthetic API/audio fixtures, not learner data. The existing
AUD-61 images provide the before reference; nine new images show all modes.

| Width | Before | Focus | Interaction | Content |
|---|---|---|---|---|
|320px|[Before](screenshots/AUD-61-wrapup-assigned-320.png)|[Focus](screenshots/AUD-62-focus-320.png)|[Interaction](screenshots/AUD-62-interaction-320.png)|[Content](screenshots/AUD-62-content-320.png)|
|390px|[Before](screenshots/AUD-61-wrapup-assigned-390.png)|[Focus](screenshots/AUD-62-focus-390.png)|[Interaction](screenshots/AUD-62-interaction-390.png)|[Content](screenshots/AUD-62-content-390.png)|
|Desktop|[Before](screenshots/AUD-61-wrapup-assigned-1440.png)|[Focus](screenshots/AUD-62-focus-1440.png)|[Interaction](screenshots/AUD-62-interaction-1440.png)|[Content](screenshots/AUD-62-content-1440.png)|

## Verification and remaining acceptance

Final required CI results are recorded in the PR. Focused local checks:132 voice/
homework backend cases passed;94 accent/consent cases passed, including Indian
routing;43 browser cases passed with the WebKit-only codec case skipped under
Chromium. Frontend unit, lint/coverage, TypeScript and build results are recorded
in the PR. Initial browser assertions were updated for secondary typed input and
collapsed publisher details; clarification tests allow the actual two recording
windows. Double-tapping Speak cannot accidentally cancel via its replacement Stop
control. A late-account fallback notice has a regression test.

Local WebKit cannot launch because system libraries are missing and sudo requires
interactive authentication. Hosted CI installs WebKit dependencies and runs the
separate iPhone-emulation project. Its synthetic native codec case uses real
AudioContext/MediaRecorder AAC and user-gesture playback where supported; a host
without AAC reports an explicit skip. WebKit emulation is not native iOS Safari,
and automated accessibility semantics/keyboard checks are not a real VoiceOver
session. [WebKit documents Safari MediaRecorder MP4/AAC support](https://webkit.org/blog/11353/mediarecorder-api/).

No new live ElevenLabs, OpenAI or YouTube synthesis/discovery test was performed.
**Production deployment remains NO-GO until the ElevenLabs401 availability issue
is resolved/verified securely and the owner reviews the implementation.** Green
mocked tests alone cannot establish that the intended production voice is audible.

After approved deployment, owner acceptance must cover:

1. On iPhone Safari, hear the custom Audli tutor during onboarding, classroom,
   feedback, invitation and confirmation; verify any genuine fallback is disclosed.
2. Explicitly request Indian English for a new passage and accept the one-passage
   offer. Confirm the passage accent and unchanged tutor voice. A saved preference
   alone must not silently grant practice.
3. Try all six requested natural choices by voice and typed text. Check one bounded
   clarification for unclear speech, cancellation and no duplicate discovery.
4. Verify all three layouts,200% text, external-link reachability, VoiceOver,
   screen locking/background interruption, microphone permissions and replay.
5. Confirm assignment in Plan, completed lesson in Review and normal next lesson.

Recovery keeps schema005 and a compatible binary. This PR adds no new JSON fields;
`fef4413` remains a schema-compatible source fallback. Do not return to pre005
binaries. Disable homework and restart Render if necessary; preserve learner data.
