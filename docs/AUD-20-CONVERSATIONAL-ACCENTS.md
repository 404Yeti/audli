# AUD-20 conversational accent practice

Accent offers use the existing WELCOME check-in and its upload, recognition,
answer, tutor speech and revision checkpoints. REVIEW also accepts learner-initiated
accent requests. No visual selection controls or additional lesson phases exist.
Preferences reuse AUD-24's exact AccentPreferences JSON contract and aliases.
This older checkout lacked that contract; compatible profile reads/writes and
resumption of its saved accents onboarding stage are restored. No separate
preference store is introduced. Existing completed accounts are not re-onboarded.

Offer policy: eligible remembered preferences only, on new lesson numbers 1, 4,
7, etc. One offer per lesson; alternate between available remembered accents
where possible. Interrupted/paused lessons consume a start and resume the same
offer/selection. Uncertain recognition never resolves an offer. A reliable
unrelated response consumes the offer without selecting. Explicit later requests
can still select an available accent. Session requests do not overwrite onboarding
preferences, difficulty, topic preferences or learner scores.

Passage voices use a server-only AUDLI_PASSAGE_VOICES JSON mapping. Each key must
be a normalized accent from app/accents.py; each entry needs an ElevenLabs voice_id
and a nonempty validation reference documenting reviewed passage audio. Production activation also re-checks mandatory human CSV review and exact
source hashes through the shared offline validation gate; a text attestation alone
cannot authorize production use. Vendor labels never establish authenticity. ElevenLabs credentials and a live provider are required. The default
mapping is empty, and AUDLI_ACCENT_PRACTICE_ENABLED defaults to false. The switch
applies equally to all authenticated learners, without an account allowlist.
See AUD-20-GLOBAL-RELEASE-READINESS.md for controls and remaining gates.
Tutor voice configuration and calls are unchanged.

Verified coverage in this workspace: **zero accents**. The user has qualitatively
approved Daniel/British, Clara/Australian and Jessica/Irish for pilot consideration
(docs/AUD-20-QUALITATIVE-APPROVAL.md); mandatory per-clip ratings remain blank.
Nothing has been enabled or changed in production. Configuration/activation and
human listening validation remain separate approval-dependent work.

Existing learners with unspecified preferences can receive a separate spoken
discovery question during WELCOME once verified voices are configured. At most
two questions across lesson history, separated by three lesson starts; resumes
reuse the same checkpoint. Discovery never selects today's accent or offers
practice in that turn. Refusal persists no_preference and suppresses future
questions/offers. Social replies leave preferences unspecified; uncertain STT
does not resolve discovery. Explicit discovery responses are saved atomically
with the lesson checkpoint and do not alter topics, scores or difficulty.
Daily offer refusal retains preferences; explicit “stop suggesting accents”
persists an opt-out. Supported learner-initiated practice remains available.

Configured passage synthesis preserves the exercise speech rate and script.
Provider HTTP failures, empty output or undecodable accent audio fall back to the
existing passage provider. The exercise stores actual accent/provider/voice ID,
validation reference and fallback flag in its content JSON, atomically with its
owned audio and lesson attachment; no database DDL is needed. Introduction speech
briefly announces runtime fallback. Replay and Retry reuse persisted audio and
routing, even if configuration changes. No script is added to public responses.

Automated fixtures establish orchestration and regression behavior, not accent
quality or actual evaluator accuracy. Spoken intent parsing is deterministic and
conservative; ambiguous replies continue the ordinary check-in. Learner-initiated
requests are supported during the existing check-in/review turns, not while a
comprehension answer is being assessed. Optional Settings controls are omitted.
