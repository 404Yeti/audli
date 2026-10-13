# Accent registry and AUD-38 release

`app/accent_registry.py` owns accent vocabulary, aliases, lifecycle and selected provider IDs. Application models, normalization and routing import the registry. Registry status alone does not authorize passage generation. Candidate IDs remain inert; an enabled selection must match its exact configured ID.

The owner approved all11 Wave1/2/3 voices for production on2026-10-13. Actual Flash2.5 English rate0.8 smoke samples and their source-ledger references are shipped in `docs/aud38-release-evidence/`. The qualitative owner decision binds both the results-ledger hash and each MP3 hash. The release does not claim a larger multi-passage/rate acoustic matrix. Existing AUD20 British/Irish/Australian evidence remains separate and unchanged.

`AUDLI_EXPANDED_ACCENT_LIBRARY_ENABLED` defaults false. Set it true to load the11 approved mappings from `AUDLI_EXPANDED_PASSAGE_REVIEW_DIR` (Docker default `data/voice-evaluation/aud38-approved`). Startup verifies complete owner approval and hashes; missing/tampered evidence or a mismatched configured ID fails closed. The existing `AUDLI_ACCENT_PRACTICE_ENABLED`, daily character ceiling, shared fleet control, concurrency and per-passage consent all remain mandatory. Registry voice records apply only to passages; tutor TTS retains its separate ID.

Supported new practice requests include “Can we practice Indian English?”, “Could we practise New Zealand English?”, “Try a Southern US accent”, “Use New York English for the next passage”, and “Can we try Boston English?”. Regional US requests resolve to one named regional accent rather than competing with general American. Refusal, cancellation and ambiguous/unsupported requests retain the authoritative AUD32 interpretation and normal fallback.

Authenticated Settings offers all14 production accent choices. Submitting preferences preserves progress, onboarding and lesson consent; preferences alone never authorize a passage. Spoken onboarding remembers named new accents without generating accent audio. Existing learners can request practice directly, without a saved preference or repeated onboarding. Actual audio success alone certifies the persisted passage voice; quota failures retain truthful normal-voice messaging. Saved audio/replay remains authoritative even if later mapping availability changes.

British `onwK4e9ZLuTAKqWW03F9`, Irish `DbwWo4rVEd5NrejHYUnm`, Australian Clara `tyepWYJJwJM9TTFIg5U7`, tutor `yKYzqEa22xh5PdidhN70` unchanged. Clara loudness replacement is tracked separately as AUD39.
