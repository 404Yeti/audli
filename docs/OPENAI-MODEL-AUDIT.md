# OpenAI model audit and modernization (AUD-100)

Scope: every OpenAI model reference in Audli 1.x and 2.0. **2.0 is changed in this branch. 1.x is audited here and not changed**
(see "1.x, separately").

## How the facts below were obtained, and how far to trust them
Read on 2026-11-04 from OpenAI's own pages (deprecations, models, pricing, structured outputs, speech-to-text guide, the `audio/transcriptions`
API reference, and the `gpt-6-luna` and `gpt-transcribe` model pages) through a fetch tool that **summarises** each page, so I could not see
the raw text. Where a page did not say something, the summary said "not on page" and so do I. Treat everything marked *docs* as strongly
indicated but not independently confirmed; the live matrix (below) is what proves it. The installed SDK (`openai` 2.54.0) was inspected
directly: it supports the `languages` transcription parameter, `responses.create` with `text.format`, `reasoning`, `store`, and `chat.completions`.

## Confirmed by the live run (2026-11-04, US$0.20)
See `docs/LISTENING-M04-LIVE-RESULTS.md`. Confirmed with real calls: all five model ids exist for the key; strict `json_schema` on the Responses API (`gpt-6-luna`, `gpt-6.1-terra`) and on Chat Completions (`gpt-4.1-mini`); `reasoning.effort = none`; `store: false`; `gpt-transcribe` with `languages=['en']` and its empty `languages` result on noise/silence/tone; the older transcription model with `language`; `json_object` generation (needs the word JSON in the input). **Not testable by calls and still taken from the docs summaries:** prices, retirement dates, zero-retention behaviour.

## Retirement status (docs)
| Model | Used by | Shutdown | Replacement named by OpenAI |
|---|---|---|---|
| `gpt-4o-mini-transcribe` (and `-2025-12-15`) | 1.x default, 2.0 default before this change | **2027-02-26** | `gpt-transcribe` or `gpt-live-transcribe` |
| `gpt-4o-transcribe`, `whisper-1` | not used | 2027-02-26 | same |
| `gpt-4.1-mini` | **1.x `llm_model` default**, rollback baseline | **2027-01-14** | `gpt-6-luna` |
| `gpt-4o-mini` | 2.0 default before this change | **2027-01-14** | `gpt-6-luna` |
| `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd` | 1.x tutor voice, onboarding | **2027-01-06** | `gpt-realtime-2.1-mini` (different workflow; FAI-48) |

Current models (docs): `gpt-6-luna` (most efficient, US$0.10 / US$0.50 per 1M in/out tokens; Chat Completions and Responses; lists structured outputs),
`gpt-6.1-terra` (US$1 / US$5; fast, efficient, writing), `gpt-6.1-sol` (US$2 / US$10), `gpt-6-astra` (US$10 / US$50). `gpt-transcribe`
(US$0.0045 per audio minute; `/v1/audio/transcriptions` only; parameter `languages` as a list; returns a `languages` array where **empty means no
language could be reliably detected**). `gpt-live-transcribe` (US$0.017/min) is realtime streaming and does not fit recorded answers.

## 2.0: selected models by task (`app/listening/models.py`, one place; every value overridable by environment)
| Task | Selected | Why | Alternatives kept configurable | Rollback |
|---|---|---|---|---|
| Transcription of a recorded spoken answer | `gpt-transcribe` | the named replacement; US$0.0045/min; file endpoint; its empty `languages` result is the signal we use for "unintelligible, treat as unknown" | `AUDLI_LISTENING_TRANSCRIPTION_MODEL` (older ids switch to the single `language` parameter automatically) | `...=gpt-4o-mini-transcribe` until 2027-02-26 |
| Spoken-comprehension assessment | `gpt-6-luna` via the Responses API with strict JSON Schema | cheapest current model, named successor of both retiring text models; the task is classification with quotes, not writing | `AUDLI_LISTENING_ASSESSMENT_MODEL`, `_API=responses|chat`, `_REASONING=''|none|minimal|low|medium` | `...MODEL=gpt-4.1-mini ...API=chat` until 2027-01-14 |
| Lesson generation (not shipped in 2.0) | `gpt-6.1-terra` (candidate) | writing quality matters more than the last cent; compared with luna in the matrix | `AUDLI_LISTENING_GENERATION_MODEL` | none needed: nothing calls it yet |

Estimated cost per answer: transcription of 20 s ≈ US$0.0015; one assessment (about 1,800 tokens in, 400 out, plus any hidden reasoning tokens) ≈ US$0.0004 on
`gpt-6-luna`. A learner finishing both lessons costs well under US$0.01. These are estimates from the pages above, not measurements.

## What changed in the code (not just model strings)
* **SDK instead of hand-built HTTP.** Typed errors, correct multipart encoding (`languages` list), `max_retries=0` (the job queue owns retries; a hidden retry would resend the learner's words), a per-call timeout (45 s default, `AUDLI_LISTENING_PROVIDER_TIMEOUT_SECONDS`).
* **Strict structured output.** Responses API `text.format = json_schema, strict: true`; the schema fixes exactly one verdict per key point, `id` and `state` as enums, `quote` string-or-null, `additionalProperties: false`. `store: false`, `max_output_tokens`, no `temperature` (not documented for the new models), `reasoning` sent only if configured. Chat Completions path (`response_format.json_schema`, `max_completion_tokens`) kept for rollback.
* **Result handling.** Refusal items → permanent. `status != completed` / `finish_reason != stop` / non-JSON / wrong shape → unusable and retried by the queue. Then the existing domain grounding still decides: ids and order must match, every claim needs a quote that is really in the learner's words.
* **Errors.** 401/403 → permanent `provider_auth`; 404 → `provider_model` (retired or misspelt id); `429 insufficient_quota` → permanent `provider_quota` (no pointless retries); 429/408/409/5xx/timeouts/connection → temporary with backoff; other 4xx → `provider_bad_request`. Operators see the specific code in the job; learners only ever see a coarse one.
* **Unintelligible speech.** An empty `languages` array from `gpt-transcribe` is stored as "no words" and the assessment model is not called (no spend, no invented transcript).
* **Worker logs** one metrics-only line per job (id, attempt, outcome, seconds) and warns at startup when a configured model has a published shutdown date.

## 1.x, separately (not changed here)
1.x reads `AUDLI_LLM_MODEL`, `AUDLI_TRANSCRIPTION_MODEL`, `AUDLI_SPEECH_MODEL` (`app/config.py`, `.env.example`). Hosted values were never read and are not changed by this branch.
A model swap is **not** a string change for 1.x:
1. **Confidence gate.** `OpenAIProvider.transcribe` asks for `include=['logprobs']` and derives `confidence` for `min_transcription_confidence` (AUD-68 short-reply rule, uncertainty warnings). Per the API reference, `logprobs` works **only** on the `gpt-4o-*transcribe` models. `gpt-transcribe` documents no equivalent, so after 2027-02-26 the gate has no input. This needs a product decision (e.g. use the empty-`languages` signal, a second opinion, or stricter text-length rules) and the AUD-68 harness re-run.
2. **Parameters.** `language='en'` becomes `languages=['en']` for `gpt-transcribe`.
3. **Structured outputs.** 1.x uses `responses.parse` with Pydantic strict schemas on `gpt-4.1-mini` pinned in evaluation records (`gpt-4.1-mini-2025-04-14`); its prompts, strict-schema shapes, latency budget (AUD-21/82) and evaluation fixtures were tuned to that model. `gpt-6-luna` must pass the AUD-77/AUD-82 evaluations before it becomes a default.
4. **Speech.** `gpt-4o-mini-tts` + voice "coral" (tutor voice, onboarding, `tutor_tts` diagnostics compare against that exact id) ends 2027-01-06; the replacement is a different (realtime) API. That is FAI-48.
5. Scripts/tests that name models are evaluation records or pins and stay as they are: `scripts/benchmark_assessment_models.py` (price table), `run_review_improvement_eval.py` (`PINNED_MODEL`), `evaluate_review_improvement.py`, `plan_voice_evaluation.py`, `tests/test_openai_provider.py`, `test_live_eval_runner.py`, `test_review_improvement_eval.py`, and the `docs/AUD-*` evidence files.

The dated deadlines (2027-01-06 / 01-14 / 02-26) make this urgent but not an emergency. A separate Linear issue tracks it with this list as its plan.
Hosted environment variables must not be changed, and nothing deployed, until that work is approved, tested and released on its own.

## Full list of model references
Code: `app/config.py:40-45` (2.0 per-task models, new), `:74-76` (1.x `llm_model`, `transcription_model`, `speech_model`), `:82,92` (ElevenLabs ids, not OpenAI); `app/services/openai_provider.py` (1.x calls); `app/services/tutor_tts.py:132`; `app/onboarding_api.py:242`; `app/improvement_api.py:119`; `app/listening/{models,providers}.py` (2.0).
Config/docs: `.env.example`, `docs/AUD-25/68/77/82-*`, `docs/AUD-68-PROVIDER-BASELINE.json` (records), `docs/LISTENING*.md`.
Scripts: listed in item 5 above. Tests: item 5 above plus the new 2.0 suites.
