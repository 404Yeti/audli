# Success mascot runtime (AUD-64 / AUD-65)

The authentic editor export originated at `/mnt/c/Users/Lenovo/untitled.riv`
(5,147 bytes; original SHA-256
`0e09c9268c894b4d95b38cfdef1c1004f3d19d8914a260318319f4340b8aa9d6`).
AUD-65 changes exactly one byte in `web/public/audli/audli-mascot.riv`: the
background SolidColor object's alpha at offset 942, from 255 to 0. Its colorValue
(property 37, object type 18, parent 44) was opaque `#282828`. The original runtime
rendered all 640,000 artboard pixels opaque; the corrected export has transparent
corners and approximately 78,000 occupied idle pixels. CSS and canvas clearing
were already transparent. No geometry, animation, timing or bindings changed.
The regression test reverses that alpha byte and verifies the original SHA-256.
Current SHA-256: `5466e2a42ac5d39ceb8edea9a9436923dc49f5b9d0d00e263f7adf2987683c2c`.
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
on errors and under either system or Audli reduced motion. Runtime resources and the pending initialization callback are
released on unmount or preference changes.

`Product` keeps the saved completion and optional spoken topic/homework/closing
wrap-up logic. Previously, `wrapupFinished` put Rive next to the statistics inside
`SessionShell`; there was no separate “Great job” heading in current main.
Now a fresh completion enters `LessonCelebration`, a full-height completion
presentation with a centered “Great job!” heading and prominent mascot. After
Rive reports `celebrate` → `idle`, the existing completion shell resumes its
optional topic/homework/closing flow and shows saved statistics, homework and
Return Home with a static mascot. Completion restore skips celebration.
No voice method or TTS request is called by the celebration.

ViewModel binding precedes state-machine playback; the trigger fires after the
first advance, preserving the iPhone initialization fix. A per-account/lesson
session-storage key and in-memory guard prevent replay. Already-consumed triggers
advance directly to results. System or app reduced motion consumes the celebration
and immediately shows results without loading Rive. Changing system preference
while waiting also bypasses immediately. Continue to results is always available.
Failed assets retain the static fallback, then advance; a 15-second presentation
watchdog covers failed imports or missing events, independently of saved progress.
Timers and listeners are cleaned up on unmount.

The presentation box is square, `min(98vw, 50svh, 520px)`. Its canvas extends 10% beyond
the box on each side to compensate for exported artboard whitespace, with all
animated artwork remaining inside the visible box. `Fit.Contain` and device-pixel
ratio sizing remain owned by the official runtime. Results use the existing static
SVG. The pinned runtime still supports state-change events; migration to a future
runtime that removes them will require an exported completion binding/event.

## Verification

AUD-65 browser checks use mobile Chromium at 320 × 844, 390 × 844 and 430 × 844.
They hold the export until observation starts, sample alpha and artwork bounds
through the flip, measure one full rotation, assert no clipping in the scaled
presentation box, then require automatic results and no canvas replay. Separate
checks cover restore, failure of either asset, app/system reduced motion,
preference change during loading, immediate Continue, Return Home and unchanged
voice calls. Synthetic review screenshots are in `docs/screenshots/AUD-65-*.png`.
Unit coverage includes binding order, one-shot memory, skipped claims and exact
background-only binary correction. Real-device iPhone Safari acceptance remains
a review step; Chromium is not Safari.

The editor source and vector artwork were not modified. Future editor exports
should disable the background fill before export. Keep the exported names and
ViewModel contract when producing future versions.
