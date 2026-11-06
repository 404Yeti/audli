# Accent compatibility inventory: Audli 1.x voices in Audli 2.0 (AUD-108)

Source of truth in code: `app/accent_catalog.py` (data only; 1.x `app/accent_registry.py` and 2.0 `app/listening` both read it). Nothing here enables an accent:
a 2.0 accent becomes generatable only when its pair in `LISTENING_PAIRS` is `enabled`, a reviewed code change made after you have listened to a real two-speaker lesson.
**No audio was generated and no credits were spent** for this inventory. "Proposed" voices come from ElevenLabs library metadata (labels/descriptions), which is
not proof of the accent. Model: 2.0 synthesises with `eleven_flash_v2_5` (the model 1.x's approved samples were made with).

## Already live in 2.0 (unchanged)
| Accent | Speaker 1 | Speaker 2 | Status |
|---|---|---|---|
| American | `EXAVITQu4vr4xnSDxMaL` | `CwhRBWXzGAHq8TQ4Fs17` | enabled, in production |
| British | `Xb7hH8MSUJpSbSDYk0k2` | `onwK4e9ZLuTAKqWW03F9` (1.x British voice) | enabled, in production |
| Australian | `tyepWYJJwJM9TTFIg5U7` (1.x Clara; owner notes it is quiet, AUD-39) | `IKne3meq5aSn9XLyUdCD` | enabled, in production |

## Candidates: 1.x voice reusable as speaker 1, second voice proposed
| Accent | 1.x voice (speaker 1) | 1.x character check | Proposed speaker 2 (alt.) | Model compat (Flash 2.5) | Quality review | Rollout readiness |
|---|---|---|---|---|---|---|
| Irish | `DbwWo4rVEd5NrejHYUnm` Jessica Gallagher: female, "clear and neutral", e-learning | natural, suitable | `BOt7zZh6gzfWlIUYnyPz` Darragh, male, neutral, calm, casual (alt. `fNG1TqnY9mWgIFIExmZA` Andrew EMTS, clear teaching) | 1.x voice: verified (AUD-20 evidence). Proposed: not yet checked | none for 2-speaker | **Not ready**: needs voice check + owner listening |
| Canadian | `RorawgRgSS2YxWqy31vY` Catherine: educational and conversational | natural, suitable | `bE3COCGjWQ0g9MBhv2cf` Jeff, male, "neutral Canadian" (alt. `D7u5tN2bpWtEVjAD7c1M` Aaron, educational) | 1.x: verified (AUD-38). Proposed: not yet checked | none | **Not ready** |
| Scottish | `csXxiUN2BUFflsCaDxPM` Chris: "Scottish Casual" | **flag**: casual style, intelligibility for ESL to be judged | `TVmbglAk3F1GkiCoOq47` Isla Skye, female, Edinburgh, e-learning (alt. `2Cg6E8rBwJfseh8Mk7sj` Nicola, West of Scotland, neutral) | 1.x: verified. Proposed: not yet checked | none | **Not ready**; owner to confirm the casual 1.x voice |
| Indian | `fydPJq00SigRIaPxWgiS` Pooja: "Upbeat UGC Creator" (female) | **flag**: social-media creator style; may not suit neutral dialogue | `eH0983ZXSi0hJkuHbC3i` Vishesh, male, clear, conversational (alt. `fSTPf9bYe9OOsdVPpz21` Lavkush, calm, articulate) | 1.x: verified. Proposed: not yet checked | none | **Not ready**; owner to confirm or replace speaker 1 |
| New Zealand | `pDZ0CqONaFi2LrK1f413` Torey Slatter (gender not recorded) | unknown; check | `XcScRctIXQsuUPQQwzsz` Becca, female Kiwi, conversational, if Torey is male; else `UqC26gfNvU9pUmY9theE` Jonty, male, e-learning | 1.x: verified. Proposed: not yet checked | none | **Not ready** |

## Not proposed for 2.0 dialogue (owner decisions needed)
| Accent | 1.x voice | Why held back |
|---|---|---|
| Southern US | `9XNfIsDIvdnSURk8DW4z` "Wade - Southern Backwoods Neighbor" | **character voice** |
| New York | `QzTKubutNn9TjrB7Xb2Q` "Jerry B. - New York Italian Mobster" | **character voice** (stereotype) |
| Boston | `UZvBfqEdvCFLqsBOo9Zr` "Jerry B. - Boston Accent Conversational Voice" | shares a voice with New York; check |
| Chinese / Japanese / Filipino / Nigerian | `1a0nAYA3...` "Ziyu - Friendly but Unclear", `DiHqGqDd...` Yuki, `7IA6NDxe...` Amara, `8P18CIVc...` "Ola - Nigerian Storyteller" | accents shaped by a learner's first language: a separate product and respect decision; one is literally named "Unclear"; storyteller is a narration style |
| Welsh, South African | none | no approved voice and no candidate; refused |

## What happens to a request
"Create a B1 lesson using an Irish accent" is understood (accent = Irish) and the review screen says "Irish English isn't available for lessons yet, so we picked American.
You can change it." The server rejects `accent: irish` outright until the pair is enabled. A place on its own ("a doctor in Canada") is not treated as an accent.

## To validate a pair (needs your explicit approval: paid ElevenLabs synthesis)
`python -m scripts.verify_generation_voices --synthesize --ids irish=DbwWo4rVEd5NrejHYUnm,BOt7zZh6gzfWlIUYnyPz` makes one ~45-character request per voice (about 25 credits
each on Flash 2.5; about 50 per accent, 250 for all five, roughly US$0.06 at the configured price). That checks the voices work on Flash 2.5, not that the accent is right:
you then listen to a real two-speaker lesson per accent and approve in writing before `status` is changed to `enabled` in `app/accent_catalog.py`.
