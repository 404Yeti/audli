# Voice-review matrix: second voices for same-accent dialogue (AUD-115)

**Status of every second voice below: PROPOSED, SYNTHESIS-TESTED ON FLASH 2.5, NOT APPROVED, NOT ENABLED.** Speaker 1 is always your Audli 1.x voice, unchanged. A second voice is used for same-accent
two-speaker conversations only after you approve it (then one line in `app/accent_catalog.py`: `LISTENING_CANDIDATES[accent]['approved'] = '<voice id>'`, behind
`AUDLI_LISTENING_FORMATS_ENABLED` and the accent being listed in `AUDLI_LISTENING_FORMAT_ACCENTS`). Until then: monologues and mixed-accent conversations work for all 15 accents;
same-accent dialogue works for American, British and Australian only; asking for it in another accent explains why and offers "Make it a monologue in <accent>" or another accent.

**Reuse check:** the Audli 1.x registry and all its evidence files (AUD-20, AUD-25, AUD-35, AUD-38, `docs/voice-evaluation/candidates.json`) contain exactly one voice per accent
(`candidates.json` holds six old entries with no voice id). There is no second voice to reuse, so these are new proposals from the ElevenLabs library listing (labels and
descriptions only; **a provider's accent label is not proof of the accent**).

**Spend (provider-reported):** batch 1, five voices, 5 x 48.9951 = 245 credits = US$0.0445; batch 2 (authorized up to US$0.07), seven voices, 7 x 48.9951 = 343 credits = **US$0.0624**
(US$0.00891 each, no retries, no extra takes). Total US$0.107. All twelve were synthesised with the same line, "Good morning, could I get a large coffee, please?", on Flash 2.5, one take each.
The earlier five were not regenerated. The seven provider preview clips (free, different text) remain in `previews/` for comparison.

| # | Accent | Speaker 1 (your 1.x voice) | Speaker 1 sample | Proposed speaker 2 | Voice ID | Alternate | Sample | Flash 2.5 | Approval |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Irish | Jessica Gallagher, female `DbwWo4rVEd5NrejHYUnm` | AUD-20 passage evidence | Darragh, male, neutral | `BOt7zZh6gzfWlIUYnyPz` | Andrew EMTS `fNG1TqnY9mWgIFIExmZA` | `docs/listening-accent-review/irish-darragh.mp3` | tested OK | **pending** |
| 2 | Canadian | Catherine, female `RorawgRgSS2YxWqy31vY` | `docs/aud38-release-evidence/AUD38-canadian.mp3` | Jeff, male, "neutral Canadian" | `bE3COCGjWQ0g9MBhv2cf` | Aaron `D7u5tN2bpWtEVjAD7c1M` | `canadian-jeff.mp3` | tested OK | **pending** |
| 3 | Scottish | Chris "Scottish Casual", male `csXxiUN2BUFflsCaDxPM` (flagged: casual style) | `AUD38-scottish.mp3` | Isla Skye, female, Edinburgh | `TVmbglAk3F1GkiCoOq47` | Nicola `2Cg6E8rBwJfseh8Mk7sj` | `scottish-isla-skye.mp3` | tested OK | **pending** |
| 4 | Indian | Pooja "Upbeat UGC Creator", female `fydPJq00SigRIaPxWgiS` (flagged: creator style) | `AUD38-indian.mp3` | Vishesh, male, light accent | `eH0983ZXSi0hJkuHbC3i` | Lavkush `fSTPf9bYe9OOsdVPpz21` | `indian-vishesh.mp3` | tested OK | **pending** |
| 5 | New Zealand | Torey Slatter, **male** (confirmed from provider metadata) `pDZ0CqONaFi2LrK1f413` | `AUD38-new_zealand.mp3` | Becca, female, conversational | `XcScRctIXQsuUPQQwzsz` | Jenna `7R9vnLAE0ZSpY0xWp0mv` | `new-zealand-becca.mp3` | tested OK | **pending** |
| 6 | Chinese | Ziyu "Friendly but Unclear", female `1a0nAYA3FcNQcMMfbddY` (flagged: the voice name) | `AUD38-chinese.mp3` | Lee's voice, male, young, Mandarin accent | `mBoVD3461U2BagYEwjeo` | Wenchang `NxUQ5SgevJvRsX9HsM0O` (gender unlabelled) | `chinese-lee.mp3` (Flash 2.5; free preview in `previews/`) | tested OK 2.37 s | **pending** |
| 7 | Japanese | Yuki "Gentle and Precise", female `DiHqGqDdN8tWkUxlsPeg` | `AUD38-japanese.mp3` | Tatsuya Watanabe, male, young, Japanese accent | `69Na567Zr0bPvmBYuGdc` | Tahm `1MPZGta71qtdTm2BpYUl` | `japanese-tatsuya.mp3` | tested OK 2.55 s | **pending**; weakest candidate (listing describes it as a character-style accent voice) |
| 8 | Filipino | Amara, female, calm narrator `7IA6NDxeqmtXCKLKb5nf` | `AUD38-filipino.mp3` | JC "Calm & Friendly", male, young | `84RXm9MY2lATjoJi5K8P` | Jabby `DBL96vXYYbWbRYCYG3wC` | `previews/filipino-jc.mp3` | tested OK 2.88 s | **pending** |
| 9 | Nigerian | Ola, male, "Storyteller" `8P18CIVcRlwP98FOjZDm` (flagged: narration style) | `AUD38-nigerian.mp3` | Stella, female, young, warm conversational | `2vbhUP8zyKg4dEZaTWGn` | Taiwo O `E7AdVOKapxpnuchBH9Fa` | `previews/nigerian-stella.mp3` | tested OK 2.28 s | **pending** |
| 10 | Southern US | Wade "Backwoods Neighbor", male `9XNfIsDIvdnSURk8DW4z` (flagged: character voice) | `AUD38-southern.mp3` | Dr. Kendall, female, soft Southern accent, steady pace | `BVA1oNX6xZt6o7QaUwxr` | Leah `5xZj4vEjtKaYKe3vCreA` | `previews/southern-dr-kendall.mp3` | tested OK 2.04 s | **pending** |
| 11 | New York | Jerry B. "Italian Mobster", male `QzTKubutNn9TjrB7Xb2Q` (flagged: character voice) | `AUD38-new_york.mp3` | Alise, female, young, subtle NYC tone | `DZ2ULBbuhpX6zJYCQic4` | Zuri `C3x1TEM7scV4p2AXJyrp` | `previews/new-york-alise.mp3` | tested OK 3.20 s | **pending** |
| 12 | Boston | Jerry B. "Boston Accent", male `UZvBfqEdvCFLqsBOo9Zr` | `AUD38-boston.mp3` | Shelly, female, middle-aged, Boston native | `6B43gMdWDCu6YLdbVZFc` | none found (only male Boston voices besides speaker 1) | `boston-shelly.mp3` | tested OK 2.37 s | **pending**; only one female candidate |

Speaker-1 gender is from the provider's metadata (Ziyu, Yuki, Amara, Pooja, Catherine, Jessica female; Ola, Wade, Jerry B., Chris, Torey male); each proposal is the opposite gender so the two speakers are easy to tell apart.

## What you can activate right now
Nothing new: no second voice is approved. Already live for same-accent dialogue: American, British, Australian. Everything else works as monologue and mixed-accent.

## Review sheet (per proposal, "no" to any of the first four means do not approve)
1. Natural, not a character or advert. 2. The accent is the named one, steadily. 3. A B1 learner can follow it at normal speed. 4. Clearly different from speaker 1.
5. Any exaggeration or caricature (flag, not delete). 6. Suitable for ESL listening.

## How to approve
Reply with the accents and voice ids (the proposal, or the alternate) you approve. I then set `approved` for exactly those, run the per-accent tests, merge, and verify
`/api/health` shows `conversation` including them. Optionally approve the US$0.062 Flash 2.5 check first for rows 6-12.

## Validation results (all twelve)
* **Compatibility:** all twelve synthesised on `eleven_flash_v2_5`, each returning a valid MP3 of 2.0-3.2 s for the 49-character line; no failures.
* **Voice identity:** for every request the provider returned the requested voice id and name (e.g. `69Na567Zr0bPvmBYuGdc` / "Tatsuya Watanabe", `6B43gMdWDCu6YLdbVZFc` / "Shelly").
* **Not verifiable by machine:** that the accent is the named one, that it is natural or intelligible to learners, and speaker distinction. That is your listening review.

## Japanese and Boston (called out)
* **Japanese, Tatsuya Watanabe:** compatible, but the listing calls it a character-style accent voice and the marketing says "a noticeable accent different from other characters". Alternates (unsampled, metadata only):
  Tahm `1MPZGta71qtdTm2BpYUl` (calm narration, labelled Japanese), Taro `UznIBkKIQe3ZG2tGydre` ("true Japanese accented English", higher pitch), Hideo `FMgBdHe1YV2Xi0B9anXW` (emphasised vowels, character-like).
  If Tatsuya sounds wrong, Tahm or Taro are the next to try.
* **Boston, Shelly:** compatible and the only female Boston voice found. If she is unsuitable, the alternates are male like your speaker 1, so they differ by voice not gender (still two distinct voices):
  Jonathan Bowen `Ufjib38uSsKFbrDM99ro` (calm, restrained, "avoiding exaggeration"), Boston Patrick `nrpkgNCrVFdOm3K0SqBQ`, Pereira `mksYbFDhHsciAVdAPydq`. Boston Bob and "Southie" were rejected as character or irate voices.
* **Budget left from the authorization: about US$0.008**, less than one more sample (US$0.0089), so no alternate was sampled. Each alternate sample would need a new approval.
