# Live validation results (2026-11-04)

Run under the owner's approval (`docs/listening-live-approval.json`): cumulative ceiling US$5.00, dedicated key supplied through the environment (never printed or stored).
**Actual spend: about US$0.2005** in total (two pre-flights US$0.0036 each + the full matrix US$0.1933), computed from the token and audio-second usage the provider reported
times the price table in `app/listening/models.py`. It is not an invoice: check the OpenAI dashboard. 260 calls in all (8 + 8 + 244). No learner data was involved: every input is a synthetic
transcript from `docs/listening-live-cases.json` or a locally generated clip (espeak-ng speech, noise, silence, tone).

## Compatibility (what the pre-flight and the run proved)
| Item | Result |
|---|---|
| `models.retrieve` for `gpt-6-luna`, `gpt-6.1-terra`, `gpt-4.1-mini`, `gpt-transcribe`, `gpt-4o-mini-transcribe` | all five available to this key |
| Responses API, `text.format = json_schema, strict: true`, `store: false`, `max_output_tokens`, no `temperature` | accepted by `gpt-6-luna` and `gpt-6.1-terra`; 0 malformed responses in 150 calls |
| `reasoning: {effort: "none"}` on `gpt-6-luna` | accepted |
| Chat Completions rollback path (`response_format.json_schema` strict, `max_completion_tokens`, `store: false`) on `gpt-4.1-mini` | accepted; 0 malformed in 30 calls |
| `gpt-transcribe` with `languages=["en"]` via the SDK | accepted; speech returned text with `languages` populated; **noise, pink noise, silence and a tone all came back with an empty `languages` list and no words**, which the pipeline treats as unknown |
| `gpt-4o-mini-transcribe` with `language="en"` (rollback) | accepted; it returns no signal (always "reliable") but also no invented words on those synthetic clips |
| Generation with `text.format = json_object` | **was refused** (400: input must contain the word "json"); fixed in the harness prompt; then 12/12 valid `LessonContent` and 12/12 level fit for both `gpt-6.1-terra` and `gpt-6-luna` |
No code change to the product adapters was needed. The only correction was in the harness's generation prompt.

## Assessment quality, against the criteria set before the run
Numbers are verdicts judged against `allowed` states per key point. "Credit" means `covered` or `partly`.
| Row | Model | Pass | correct | incorrect | incomplete | ambiguous | unintelligible | malformed | credited when it should not be | latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | `gpt-6-luna` (3 repeats) | 79/90 | 12/18 | 18/18 | 13/18 | 18/18 | 18/18 | 0 | **0** | 2.1 s / 3.8 s |
| A2 | `gpt-6-luna`, reasoning none | 27/30 | 4/6 | 6/6 | 5/6 | 6/6 | 6/6 | 0 | 0 | 1.5 s / 3.7 s |
| A3 | `gpt-6.1-terra` | 27/30 | 4/6 | 6/6 | 5/6 | 6/6 | 6/6 | 0 | 0 | 2.4 s / 4.3 s |
| A4 | `gpt-4.1-mini` (chat; retires 2027-01-14) | 29/30 | 5/6 | 6/6 | 6/6 | 6/6 | 6/6 | 0 | 0 | 1.3 s / 2.2 s |
Pre-registered bars: correct ≥ 80%, incomplete ≥ 80%, the other three ≥ 90% with zero false credit, malformed ≤ 2%, repeat-consistency ≥ 90%, p95 < 20 s.
* **Met:** no wrongly credited answer in any row (incorrect, ambiguous and unintelligible all 100%, zero `false_covered`); 0% malformed; latency far under the bar; grounding never had to downgrade a claim (`ungrounded_claims_downgraded` = 0).
* **Not met as written, for `gpt-6-luna`:** `correct` 67% and `incomplete` 72% (bars 80%), and repeat consistency 25/30 = 83% (bar 90%). `gpt-6.1-terra` missed the same two bars; `gpt-4.1-mini` met `correct` (83%) and `incomplete`.
* **Why, from the individual verdicts:** every one of the 11 misses in A1 is on a **compound key point** where the learner said some but not all components, e.g. the point "Tom has a cough, a sore throat and a small fever" against "He is sick with a bad cough and a fever". All four models, independently, said `partly`. That is the accurate verdict under the instruction ("covered = clearly expressed"); my expectation (`covered`) was wrong. Four expectations were corrected afterwards (restaurant-correct-1 `final` in the pre-flight, then pharmacy-correct-2, pharmacy-correct-3, pharmacy-incomplete-1 and pharmacy-incomplete-2). **Re-judged from the stored verdicts, with no new call: A1 90/90, A2 30/30, A3 30/30, A4 30/30.** This is a post-hoc correction made after seeing the results; treat the table above, not the re-judged one, as the pre-registered outcome.
* **Consistency (the 5 inconsistent cases of 30):** three flip only between `missing` and `unclear` (both uncredited; the learner sees "Not mentioned" or "Not clear enough to tell"), one flips `covered` and `partly` (both credited), and one (pharmacy-ambiguous-1) flips between `partly` and `missing`, i.e. across the credit line, on a deliberately vague answer. So the decision was stable in 29 of 30 cases, but the 90% bar as written was not met.

## Working configuration (unchanged by the run)
| Task | Model | Notes |
|---|---|---|
| Transcription | `gpt-transcribe`, `languages=["en"]`, `response_format=json` | empty `languages` ⇒ stored as no words; assessment not called |
| Assessment | `gpt-6-luna`, Responses API, strict JSON Schema, `store: false`, reasoning not sent | `reasoning: none` also works and was slightly faster; not made the default on this evidence (one repeat, no cost split) |
| Rollback | `AUDLI_LISTENING_ASSESSMENT_MODEL=gpt-4.1-mini AUDLI_LISTENING_ASSESSMENT_API=chat`, `AUDLI_LISTENING_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe` | both proven to work today; both retire in early 2027 |
| Lesson generation | `gpt-6.1-terra` or `gpt-6-luna` | both produced valid, level-fit lessons 12/12; nothing in 2.0 calls it yet; luna is far cheaper |

## What this does not show
* **Synthetic inputs only.** Speech was espeak-ng (clean, robotic) and noise was generated; real learners, accents, background noise and microphones are untested. Real room noise may still produce invented words that the `languages` signal does not catch.
* Assessment quality on real learner speech and on lessons other than these two.
* Prices, retirement dates and zero-retention terms are not testable by calls. Rate limits, the real invoice, and behaviour under load are untested.
* The compound-key-point finding is also an **authoring rule** for future lessons: write single-idea key points, or accept `partly` as the normal result for a partial answer.
