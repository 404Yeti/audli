# Audli 2.0 Milestone 04: real-world integration and beta preparation (plan, no code yet)

Nothing here has been run against a paid provider, a hosted service, a real device or production. Facts about OpenAI come from its public
docs pages as retrieved on 2026-11-04 through a fetch tool that summarises pages; **treat each as unconfirmed until the live trial below
or a direct read of the page**. Nothing in this plan is approved spend.

> **Superseded in part:** findings A1-A6 and A8 are addressed by the model modernization branch; see `docs/OPENAI-MODEL-AUDIT.md` and `docs/LISTENING-M04-LIVE-MATRIX.md` (budget now sized there). A7 (global ceiling and kill switch) and A9 (provider disclosure line) are done: `docs/LISTENING-AI-SAFEGUARDS.md`.

## 1. Audit of the OpenAI adapters (`app/listening/providers.py`)

| # | Finding | Evidence | Risk | Fix |
|---|---|---|---|---|
| A1 | **Both default models are scheduled for shutdown.** `gpt-4o-mini-transcribe`: 2027-02-26 (replacement `gpt-transcribe`). `gpt-4o-mini` (my assessment default) and `gpt-4.1-mini` (the 1.x `llm_model`): 2027-01-14 (replacement `gpt-6-luna`). The 1.x defaults `gpt-4o-mini-transcribe` and `gpt-4o-mini-tts` are affected too (only partly tracked in FAI-48). | OpenAI deprecations page | Beta would break on the shutdown dates | Keep models in config (already), default to the replacements only after the live trial confirms them; file a 1.x follow-up |
| A2 | Transcription request uses `language='en'`. The docs say `gpt-transcribe` takes `languages` and that both must not be sent. | speech-to-text guide | A 400 on every answer | Send the parameter the chosen model accepts; decided by the trial |
| A3 | No confidence signal. 1.x requests `include=['logprobs']`; the guide for the new model does not document it. Unintelligible audio can come back as a confident-looking hallucination (known STT failure on noise/silence). | speech-to-text guide; `openai_provider.py:293` | A wrong "What Audli heard" shown to a learner | Keep existing decode/RMS/duration gates; add a speech-ratio gate; the trial's unintelligible set decides whether more is needed; never show a transcript as fact when words < 3 (already: unknown) |
| A4 | Assessment uses Chat Completions `json_object` + `temperature: 0`. Newer models may reject `temperature`; `json_object` guarantees JSON, not our shape. | model page lists structured outputs; `temperature` support not stated | Every assessment fails as `provider_refused` | Use strict structured output (`json_schema`, enum states, fixed ids, `additionalProperties:false`), no `temperature`, `max output tokens`, `store:false` (1.x already sets `store=False`), small `reasoning` effort if supported. Domain grounding stays the final authority |
| A5 | Error mapping: 408/409 treated as permanent; `429 insufficient_quota` is retried 4 times (it can never succeed). | `_classify` | Wasted retries, no operator signal | 408/409 → temporary; quota/auth (401/403/insufficient_quota) → permanent **and** a distinct failure code the operator sees |
| A6 | **The worker logs nothing.** A failing provider is invisible. | `worker.py` | Silent beta failures | One metrics-only line per job: id, outcome, code, stage timings. Never transcript, audio, names or ids of learners |
| A7 | Cost control is per learner only (30 recordings/24 h). No global ceiling. | `claim_upload` | One bug = unbounded spend | Global daily job ceiling (database count) and an operator kill switch that stops enqueue/assess without a deploy |
| A8 | Timeouts: 60 s per provider call, lease 120 s, heartbeat before each stage. Worst case is safe (each call starts on a fresh lease) but there is no total budget. | `spoken.process` | A slow provider holds a worker | Per-call 45 s, per-job wall-clock cap; keep heartbeat |
| A9 | Disclosure: the screen says the recording is "turned into text, then deleted". It also goes to a third-party API. | `SpokenExercise.tsx` | Privacy/consent gap | Plain-language line naming the provider; privacy review of the provider's retention terms (not verified here) |

Response validation already in place and tested: ids and order must match the rubric exactly, states are an enum, every claim needs a
quote that is a whole-word substring of the learner's transcript or it becomes `unclear`, and under three words is "unknown".

## 2. Budget-capped live assessment test (needs your approval, a dedicated key)

* **Key:** a new OpenAI project key with a hard spend limit of **US$5**, supplied only through the environment of the person running it. Never committed.
* **Harness (to build, ~1 file):** `scripts/listening_live_trial.py`, off by default, refuses to run without `AUDLI_LIVE_TRIAL=I-APPROVE-SPEND`, counts calls and audio minutes before each request and stops at the caps below, writes a results table with no audio and no learner data.
* **Cases:** 2 lessons × 5 categories × 3 samples = **30 samples**, then 10 of them repeated 3 times for consistency = **60 more runs** (90 runs).
  1. *Correct*: clear paraphrase of the key points (different wording each).
  2. *Incorrect*: confident but wrong facts (wrong food, wrong allergy).
  3. *Incomplete*: one of three points only.
  4. *Ambiguous*: vague ("she got the thing she didn't want"), pronoun-only, hedged.
  5. *Unintelligible*: mumble, background noise only, silence with breath, non-English speech, off-topic speech.
  Samples: half synthesised locally with espeak (free, repeatable), half recorded by humans (at least two voices, one non-native accent), each 8–40 s.
* **Estimated cost** (prices as the fetched pricing page states them, unconfirmed): `gpt-transcribe` US$0.0045 per audio minute; `gpt-6-luna` US$0.10 per 1M input, US$0.50 per 1M output tokens. A 20 s answer plus a ~1,500-token assessment ≈ US$0.002. **90 runs ≈ US$0.20.**
* **Hard caps (enforced by the harness): US$1.00, 120 runs, 60 audio minutes.** Recommended approval: **US$1.00**.
* **Pass criteria:** 0 assessments containing a claim without a verifiable quote (the domain layer enforces it; the trial confirms the model rarely tries); correct samples ≥ 80% of points `covered`; incorrect samples ≤ 10% falsely `covered`; unintelligible set ≥ 90% reported as unknown/unclear and never a confident transcript of invented text; malformed responses ≤ 2%; end-to-end p95 under 30 s; the same sample gives the same verdict in ≥ 90% of repeats. Failures are reported as a table, not averaged away.

## 3. Production-quality lesson audio

* **Provider choice (recommended, needs approval):** ElevenLabs. The 1.x product already uses it for passage voices (separate code, not imported). OpenAI speech models `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts` are listed for shutdown 2027-01-06 (FAI-48), so they are a poor base.
* **Method:** keep the proven fixture method. Synthesise **each sentence separately** with the speaker's voice, join with fixed pauses, and **measure** each sentence's start/end. That gives exact transcript sync with no alignment provider and no estimation. Only `synthesise()` in `scripts/build_listening_fixture.py` changes; ffmpeg joins as today.
* **Quality gate before publishing (human):** listen to every lesson end to end; check names, numbers, pacing at 0.75×/1×/1.25×; reject mispronunciations by re-synthesising a sentence, not by editing the script's meaning.
* **Storage:** private S3-compatible bucket, content-addressed keys (`lessons/<slug>/<sha16>.mp3`, already so), signed playback URLs of 5 minutes (already so). `provider` stays recorded per variant (`elevenlabs`), `voice` records both voice ids.
* **Versioning:** published versions and audio variants are immutable. Replacing the development audio is a **new lesson version** (add an optional `revision` field to `LessonContent`, excluded from the content hash when absent), never an edit. Old attempts keep the audio they were shown. The seed script must refuse `provider == 'fixture'` when `environment == 'production'`.
* **Not done and not approved:** generating or replacing any production audio.

## 4. Physical iPhone Safari acceptance checklist (full four-exercise journey)

Device: a real iPhone on current iOS, Safari (not an in-app browser), tested on Wi-Fi and on mobile data, with a staging build that has a real backend and worker (not the Vercel preview that proxies to production; see section 5).
Record for each line: pass / fail / note / screenshot or screen recording.

1. Sign in; library shows both lessons, levels and durations; 44 pt targets; safe-area spacing at the bottom action bar; no horizontal scroll in portrait and landscape.
2. Open a lesson. Audio plays on tap; plays with the silent switch on and off (record the actual behaviour); seek, ±10 s, speed, replay; lock the screen and return; take a phone call mid-playback and return; switch apps and return; Bluetooth headphones.
3. Guided transcript follows the audio and tap-to-replay works.
4. Question 1 (choice): select, check, feedback, VoiceOver reads state.
5. Question 2 (spoken): microphone permission prompt (allow, deny, allow later in Settings); recording indicator and timer; stop; **listen back**; record again; send. Confirm the recording is `audio/mp4` and the server accepts it (worker log shows `done`). Upload on mobile data. **Airplane mode mid-upload, then reconnect and resend** (one answer, not two). Minimum (2 s) and maximum (55 s) lengths. Interruption during recording (call, Siri) fails with a clear message. Denied mic: upload-a-file fallback and skip both work.
6. The lesson continues while the answer is pending; the pending pill is announced by VoiceOver without stealing focus.
7. Question 3 (fill): keyboard appears, nothing hides the field or the button, autocorrect/autocapitalise off, Return key submits, paste works.
8. Question 4 (order): move buttons usable with a thumb, VoiceOver announces positions, the Dynamic Type largest size does not clip.
9. Summary: scored count, per-question outcomes, pending → checked updates by itself while the page stays open, and after leaving the app and returning.
10. Resume: kill Safari mid-lesson; reopen from the library → Continue lands on the next open question with answers kept; the back-swipe gesture never loses an answer.
11. Reduced Motion on: no motion-only information; Dark Mode: text remains readable (the design is light only; record what happens).
12. Failure states: switch the backend off briefly → error + Try again; worker off → answer stays "being checked", never a wrong result.
Acceptance: every line passes or has a filed issue with an owner; no line may lose a learner's answer or show a wrong result.

## 5. Production prerequisites (reviewed, none performed)

1. **Migrations.** Apply 009 then 010 as the database owner with `python -m scripts.migrate_postgres --listening` after a backup and on a rehearsal copy first. Both are additive; 010 only widens one CHECK. Ledger is `listening_migrations`, not `schema_migrations`. Rollback = restore the backup (there is no down-migration; new tables can be dropped only while empty).
2. **Worker deployment.** The API container's `CMD` runs only uvicorn. The worker is `python -m app.listening.worker` and needs its own process. A Render background worker is paid infrastructure → approval needed; fallback is a second process in the existing service (shares fate with the API, so only for a first beta). It needs the same database URL, the same S3 settings and `AUDLI_LISTENING_PROVIDER=openai` with `OPENAI_API_KEY`. With no worker, answers stay "being checked" and recordings are not swept, so the bucket lifecycle rule below is mandatory.
3. **Least-privilege database grants.** Today every process uses the owner role. A restricted role is **not just GRANTs**: RLS is enabled with no policies on the 2.0 tables, so a non-owner role sees nothing unless it has `BYPASSRLS` or policies are written. Needed privileges derived from the code: *API* SELECT on all 2.0 tables; INSERT on attempts, responses, answers, spoken, jobs; UPDATE on attempts, spoken, jobs. *Worker* SELECT on all; UPDATE on spoken, jobs. *Operator/seed* INSERT on lessons, versions, exercises, keys, rubrics, audio; UPDATE on versions. Nobody needs DELETE (account erasure cascades from `users`, run by the owner). `prepare()` already fails closed with a fixed message when privileges are missing. **Untested under a restricted role**: draft only; decide between BYPASSRLS and policies, then test on a rehearsal database.
4. **S3-compatible storage.** Private bucket, key scoped to it, https endpoint, `AUDLI_LISTENING_STORAGE=s3`. Mandatory: a **lifecycle rule expiring `listening-tmp/` after 1 day**, and **object versioning off** (or noncurrent versions expired) because deleting a recording in a versioned bucket keeps it. Presigned GET, Range and expiry against a real provider are still unverified (the earlier emulator could not do presigned requests); recording put/get/delete have only been tested against a mocked transport.
5. **Recording cleanup.** Deleted when the transcript is saved, on terminal failure, by the worker sweep (abandoned uploads 10 min, anything older than 1 h) and by the bucket rule. Add an alert if `listening-tmp/` holds objects older than 2 h.
6. **Preview isolation.** Vercel Previews share `BACKEND_URL` with Production (read earlier by name only), so a Preview of the 2.0 UI talks to the production API where the flag is off. Use a separate staging backend + database + bucket + worker for 2.0, as proposed in `docs/PREVIEW-ENVIRONMENT.md`. Not changed.
7. **Flag.** `AUDLI_LISTENING_ENABLED` stays false in production until sections 1–6 are done and accepted.

## 6. Smallest safe implementation sequence

1. **Adapter hardening, no spend** (A2–A9): structured output schema, parameter set per model, error classes, metrics-only worker logs, global ceiling + kill switch, provider disclosure line; tested against mocked transports. One PR.
2. **Live trial harness** and the 30-sample set (espeak half generated now, human half recorded by you). Run after your approval of the US$1.00 cap. Result table decides the model names and parameters. One PR with the table.
3. **Staging environment** (separate backend, database, bucket, worker) after you approve the infrastructure; run migrations 009/010 there; run the iPhone checklist and the real-S3 presign/Range/expiry test against the real bucket.
4. **Production audio** pipeline change (`synthesise()` swap, `revision`, production seed guard); generate **staging** audio after approval of the voice choice; human listening gate.
5. **Restricted database role** decision and test on staging.
6. Fix whatever the iPhone checklist finds; then a go/no-go review of the production prerequisites before any flag change. Merge order stays #121 → #122 → #123.
