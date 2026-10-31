# Transcription-concern evaluation — approval required

Status: proposal only. Neither recovery PR changes the prompt, model, evidence validation, confidence thresholds or confirmation rules. No provider calls are authorized by this document.

## Fixed dataset and limits

Use 32 synthetic, manually labelled cases (8 each): coherent incomplete/wrong comprehension; poor grammar with understandable meaning; genuinely unintelligible/ambiguous recognition; confidence/confirmation boundary cases. Include active follow-ups and earlier accepted turns to distinguish current-turn concerns from historical ambiguity. Include .54/.55 and existing confidence bands, empty text, uncertainty flags, valid near-threshold speech and the existing explicitly confirmed correction policy. Never use private learner transcripts/audio.

First run all cases through mocked STT/assessment and real application validation. Confidence gates must retain identical results. A provider evaluation requires a separate explicit approval: at most 64 assessment calls (32 cases × current/proposed contract), one pass, no automatic retries/fallback providers, no STT/TTS, same configured model and parameters. Before starting, verify actual production model/pricing and set input/output token caps; lower case count if worst-case charges exceed $2. Reserve estimated worst-case cost before each call; stop new calls at $1.50, timeout, rate limit or uncertain billing. No production lesson traffic.

## Candidate contract to evaluate, not implement

A concern must identify unintelligibility in the current turn, with useful attribution. Understandable incorrect, incomplete or poorly grammatical answers use existing misunderstood/insufficient-evidence categories. High confidence never overrides genuine ambiguity. Map any proposed structured attribution into the existing server validation; retain quote grounding, all evidence units, transcript gates and atomic progression.

## Review and decision

Blind-review paired outputs against fixed labels. Report false concerns, missed genuine concerns, evidence-category changes, false acceptance, confidence/confirmation gate parity, output tokens and provider/application latency. Require no false acceptance or confidence-gate bypass; all current-turn/historical-turn attribution cases correct; no increase in missed genuine concerns; coherent incomplete/poor-grammar false concerns reduced. Ambiguous reference labels require human review, not automatic acceptance. A passing small sample supports a separately approved limited real-world test, not a general accuracy guarantee.

Store only synthetic fixtures and aggregate results. Never log raw provider reasoning, credentials or production learner content. Any contract implementation and provider-backed run need explicit owner approval.
