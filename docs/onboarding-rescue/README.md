# AUD-43 / AUD-44 onboarding rescue

The needs stage required both an extracted learning reason and listening situation. Out-of-scope or vague goals often supplied neither. Two clarifications ended in a persisted pause, and explicit Retry reset that budget. There was no text or skip escape. Recognition failures also left voice as the only input.

The existing persisted introduction now explains the short-audio/listen/retell/help loop. Its existing heard checkpoint still prevents replay for returning learners. No swipe introduction is included.

A confirmed goal answer advances directly to interests. Missing information uses everyday listening defaults; existing saved goals/situations survive skipped or unknown answers. Writing/speaking goals stay as learner preferences, while the next spoken prompt acknowledges them and explains the listening focus. That acknowledgement is combined with the existing interests prompt, without another speech request. Scores, adaptation, accent preference discovery and separate accent consent are unchanged.

The optional goal input follows the typed-name layout. Beginning to type freezes playback/recording while the learner composes an answer. Text/Skip cancels stale client continuations. The server uses an account-scoped persisted question token: an explicit goal wins against speech until the next interests recording is committed; the first explicit submission wins against duplicate explicit requests. Stale speech uses the existing revision conflict. Retries cannot advance twice. International text is preserved. Blank submissions require Skip; control characters and oversized input are rejected.

Old paused needs checkpoints can use Text/Skip immediately. After three uncertain recognition results the client skips goals without treating uncertain speech as evidence. Failed transcription or denied microphone leaves Text/Skip available. The later interests/accent questions retain their existing voice recovery behavior.

Three optional fields are stored in the existing versioned onboarding JSON; no database migration, new provider integration or extra provider request is required. Tests use synthetic provider/recognition fixtures and do not establish live extraction accuracy.

Screenshots: [desktop](goals-desktop.png), [mobile](goals-mobile.png).
