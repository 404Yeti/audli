# Success mascot — Rive handoff (AUD-64)

No authentic `.riv` asset exists in the accepted repository at PR #46 / 42f2939.
`web/components/audli-mascot.tsx` contains the approved Figma Mascot System
(node 7:23) geometry. `web/public/audli/success.svg` is static SVG, not Rive.

`RiveMascot` is a reusable, dependency-free boundary with `idle` and `celebrate`
presentation states. Both currently render the existing vectors as a static pose.
It uses the same element, dimensions and margins. Only the finished success view
uses it; topic check-in, homework and closing retain their existing voice cues.
There is no asset request, canvas or runtime. Static poses cannot replay on
rerender or restoration and respect system and Audli reduced-motion settings.

## Create manually in Rive

Import `web/public/audli/mascot-source.svg`, copied directly from the existing
component's neutral geometry. The older `success.svg` is a different pose; use
the supplied neutral source as the identity reference. Preserve its 172.8 × 151.2 proportions,
asymmetric ear pieces, shapes, spacing, colors and white center. Do not redesign
the mascot or add a face, limbs or accessories. Keep movement within the original
artboard bounds to preserve layout.

| Item | Exact name / behavior |
| --- | --- |
| Export | `web/public/audli/mascot.riv` — authentic editor export |
| Artboard | `AudliMascot` |
| State machine | `AudliMascotMachine` |
| Default state / animation | `idle`: gentle, subtle looping movement |
| One-shot state / animation | `celebrate`: happy bounce and little dance, 2 seconds, no loop |
| Trigger input | `celebrate`: transition from idle to celebrate |
| Completion transition | Return automatically to idle at the end of celebrate |

Export a neutral first frame matching the approved geometry. Keep the file
self-contained and silent: no audio, external assets or pointer-triggered
celebrations. Supply the `.riv` and editor source for visual review.

## Integration after delivery

This PR does not install or implement an untestable runtime adapter. Use the
[official React runtime](https://rive.app/docs/runtimes/react/react), choosing
the renderer after inspecting the export and checking iPhone Safari. Load it
inside `RiveMascot`; retain the static fallback for loading, failure, unsupported
rendering and reduced motion, with the same sizing and decorative aria-hidden role.

Before enabling motion, pass an account-scoped lesson completion identity and a
fresh-completion flag from the existing saved checkpoint. After runtime readiness,
fire `celebrate` only once for that fresh completion. Use a ref guard plus browser
presentation memory keyed by account and lesson, marking it before firing so
Strict Mode, rerenders and remounts cannot repeat it. Restored/revisited
completions start idle. If storage is unavailable, fail closed to idle on
restoration. Never update lesson/database state for animation. A suppressed
celebration must not run when motion is later re-enabled.

Check both `prefers-reduced-motion` and Audli's setting in JavaScript; CSS cannot
stop a canvas. Release runtime resources on unmount and fall back immediately if
reduced motion changes during playback. Verify readiness delays, failures, Strict
Mode, reload/revisit, a second fresh lesson, account switching, both preferences
and real iPhone Safari before enabling the runtime. The static fallback currently
needs no playback guard because it performs no animation.
