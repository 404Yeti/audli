# Audli 2.0 listening formats (AUD-112)

Three formats, one pipeline. Nothing about the working generator, its budgets, worker or models changes.

| Format | Stored as | Voices needed | Offered when |
|---|---|---|---|
| Monologue | `format: story`, one speaker | one approved voice for the accent | the accent has an approved voice AND is open (below) |
| Conversation | `format: dialogue` or `conversation`, `accent2` empty | the accent's **two distinct** voices (`LISTENING_PAIRS`) | the pair is `enabled` (today: American, British, Australian) |
| Mixed-accent conversation | two-speaker format + `accent2` | one approved voice per accent, the two voices must differ | formats flag on, both accents open |

`story` stays the stored value of a monologue so existing lessons and requests are untouched; "monologue" is the learner-facing name. Monologues already worked end to end
(one narrator, validated, one voice) for the original three accents; this change adds per-speaker accents and the accents the owner curated for Audli 1.x.

## Voices (`app/accent_catalog.py`, data only)
* Monologue / mixed-accent speaker: the first voice of the accent's production pair, else the **owner-approved Audli 1.x voice** (`single_voices()`): Irish, Scottish, Indian,
  Canadian, New Zealand, Chinese, Japanese, Filipino, Nigerian, Southern, New York, Boston. Welsh and South African have no approved voice and are never offered.
* 1.x voices the owner curated are kept exactly as they are; `REVIEW_FLAGS` marks the ones to listen to (character, creator or casual style, a voice named "Unclear", quiet
  Australian, unrecorded gender). Flags are a to-do for the listening review, not a removal.
* Same-accent two-speaker conversations still need two approved voices: the five proposed second voices stay `proposed` until you approve them.
* Nothing is substituted: an unavailable combination is refused by the API (422), failed before any spend if it becomes unavailable after acceptance, or, on the natural-language
  path, replaced visibly with a note on the review screen before anything is generated (`accent_unavailable`, `mixed_unavailable`, `requested_accent*`).

## Rollout (feature flag, off by default = today's behaviour exactly)
`AUDLI_LISTENING_FORMATS_ENABLED=true` opens mixed-accent conversations. `AUDLI_LISTENING_FORMAT_ACCENTS=irish,indian,...` opens those accents for monologues and mixed-accent
speakers (original three always open). With the flag off the availability endpoint reports exactly what production offers today. Open accents one at a time after listening
review (AUD-111). Budgets, per-learner limits and the worker are unchanged.

## What is stored and shown
Each new lesson's speakers carry their `accent` (optional; older lessons have none and hash identically). The audio variant's accent is the first speaker's; the library lists
every speaker's accent. The learner-facing speaker model exposes the accent id only (no voice ids, no provider data). Script prompts are told each speaker's accent only for how
the audio sounds: no phonetic spelling, no accent-based identities; monologue questions may not refer to other speakers.

## Tests
`tests/test_listening_formats.py`: availability matrix, spec validation, voice assignment (never shared, never substituted), notes on the natural-language path, jobs with mocked
synthesis for all three formats (voices per sentence, speaker accents, cue sync, four exercises), refusal before spend if the flag turns off, API contracts, privacy, playing a
mixed lesson to completion with normal scoring.
