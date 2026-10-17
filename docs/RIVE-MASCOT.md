# Success mascot runtime (AUD-64)

The authentic editor export is `web/public/audli/audli-mascot.riv`, copied byte-for-byte
from `/mnt/c/Users/Lenovo/Music/untitled.riv` (15,496 bytes).
SHA-256: `8bd221473b92b8dfaf3f143fcdea2b303c857ce0be84a78544066d585dde494e`.
The earlier Documents, Pictures and Videos exports failed runtime verification and are not shipped.

| Contract | Name / behavior |
| --- | --- |
| Artboard | `AudliMascot`, 800 × 800 |
| State machine | `AudliMascotMachine` |
| ViewModel | `AudliMascotControls`, default exported instance |
| Trigger property | `celebrate` (data binding, not a classic input) |
| Default animation | `idle`, subtle looping motion |
| Celebration | `celebrate`, two-second backflip, automatically returns to idle |

`RiveMascot` dynamically loads the official React canvas runtime 4.36.0 / canvas
2.44.0 only when motion is permitted. The matching WASM is self-hosted as
`/audli/rive.wasm`; no asset CDN is needed. On runtime upgrades, replace this WASM
with the matching package file. The original SVG remains visible while loading,
on errors and under either system or Audli reduced motion. Runtime resources and
pending celebration callbacks are released on unmount or preference changes.

The finished success view passes the saved lesson identity, account scope and a
fresh-completion presentation flag. ViewModel binding precedes state-machine
playback. This exact export includes brief Rive startup branding; the celebration
waits for 2.5 seconds of rendered runtime time so the backflip is visible afterward.
A session-storage key and in-memory guard are claimed before firing. Rerenders,
remounts, restored completions and re-enabling motion cannot replay a celebration.
Storage is optional and has no effect on lesson or database state.

The existing mascot box, SVG proportions, margins and surrounding layout remain
unchanged. `Fit.Contain` centers the square artboard within that box; the React
runtime resizes for device pixel ratio. Statistics, homework, navigation and voice
behavior are unchanged.

## Verification

The existing official 2.44.0 Chromium harness at a 390 × 844 viewport and 300 × 300
canvas passed two separately triggered celebrations. Each returned to centered
idle at about two seconds and remained centered for over three seconds, without
edge clipping, displacement or cumulative drift. Passing frames were not saved.
The startup branding was excluded from mascot clipping measurements.

Focused frontend checks cover one-shot ViewModel binding and presentation memory,
separate lessons/accounts, storage failure, reduced motion, mobile container
bounds/rotation, restored completion and failed asset/WASM loading. Real-device
iPhone Safari acceptance remains a review step; Chromium is not Safari.

The editor document and vector artwork were not modified. No manual Rive editor
work is required for this candidate. Keep the exported names and ViewModel contract
when producing future versions.
