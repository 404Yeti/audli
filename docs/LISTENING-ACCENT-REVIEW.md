# Accent voice verification and listening review (AUD-111, AUD-114)

**Spend:** 5 samples x 49 credits = **245 credits = about US$0.045** (ElevenLabs estimate: US$0.00891 each), against your US$0.10 authorization. One take per voice, no retries,
Flash 2.5 (`eleven_flash_v2_5`, the model Audli 2.0 uses), line: *"Good morning, could I get a large coffee, please?"* (the repo's own verification line). No other synthesis was run
and no accent was enabled.

## What this proves, and what it cannot
* **Proven:** each proposed second voice is available to the account and synthesises on Flash 2.5, returning a valid MP3 of plausible length (2.1-2.6 s for 49 characters).
* **Not proven, by design:** that the accent is what the label says, or that it sounds natural and clear to a learner. Only a human can judge that. I did not listen, and the provider's
  accent label is not evidence. **No accent can be enabled until you have listened and approved.**

## Samples (in `docs/listening-accent-review/`)
| Accent | Proposed speaker 2 | Sample | Speaker 1 (your 1.x voice) for comparison |
|---|---|---|---|
| Irish | Darragh, `BOt7zZh6gzfWlIUYnyPz` (male, neutral, calm) | `irish-darragh.mp3` | Jessica Gallagher `DbwWo4rVEd5NrejHYUnm` (female); AUD-20 evidence |
| Canadian | Jeff, `bE3COCGjWQ0g9MBhv2cf` (male, "neutral Canadian") | `canadian-jeff.mp3` | Catherine `RorawgRgSS2YxWqy31vY`; `docs/aud38-release-evidence/AUD38-canadian.mp3` |
| Scottish | Isla Skye, `TVmbglAk3F1GkiCoOq47` (female, Edinburgh) | `scottish-isla-skye.mp3` | Chris "Scottish Casual" `csXxiUN2BUFflsCaDxPM`; `AUD38-scottish.mp3` |
| Indian | Vishesh, `eH0983ZXSi0hJkuHbC3i` (male, light accent) | `indian-vishesh.mp3` | Pooja "Upbeat UGC Creator" `fydPJq00SigRIaPxWgiS`; `AUD38-indian.mp3` |
| New Zealand | Becca, `XcScRctIXQsuUPQQwzsz` (female, conversational) | `new-zealand-becca.mp3` | Torey Slatter `pDZ0CqONaFi2LrK1f413` (gender not recorded); `AUD38-new_zealand.mp3` |

Becca assumes the 1.x NZ voice is male. If it is female, swap speaker 2 for Jonty `UqC26gfNvU9pUmY9theE` (not sampled: no spend beyond the five).

## Review sheet (tick per voice; "no" on any of the first four means not enabled)
1. **Naturalness**: sounds like a real person, not a character or an advert.
2. **Accent consistency**: the accent is the named one, steadily, not drifting.
3. **Intelligibility**: a B1 learner could follow it at normal speed.
4. **Speaker distinction**: clearly different from speaker 1 (gender, pitch, pace).
5. **Exaggeration**: any caricature or character style? (Flag, do not delete.)
6. **ESL suitability**: pace, clarity, and no slang-heavy delivery.

## Existing voices flagged for the same review (kept exactly as you curated them)
Indian "Upbeat UGC Creator", Scottish "Casual", Southern "Backwoods Neighbor", New York "Italian Mobster", Boston (shares a name with New York), Chinese "Friendly but
Unclear", Nigerian "Storyteller", Australian Clara (quiet, AUD-39). `REVIEW_FLAGS` in `app/accent_catalog.py` records why; none is removed or replaced.

## Controlled rollout once you approve (one accent at a time; no code change needed for monologues)
1. Monologue/mixed-accent for an accent whose 1.x voice you are happy with: set `AUDLI_LISTENING_FORMATS_ENABLED=true` and add the accent to `AUDLI_LISTENING_FORMAT_ACCENTS`
   (needs PRs #140 and #141 deployed). Watch the ledger and one generated lesson.
2. Same-accent two-speaker conversation for an accent: a reviewed change flipping its `LISTENING_PAIRS` entry to `enabled` with the approved second voice.
