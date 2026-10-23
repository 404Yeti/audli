# AUD-82 — Assessment model benchmark (capped, paid)

Production model **unchanged** (`gpt-4.1-mini`). Review the findings before any change.

## Method and limits
* Production prompt, strict schema and `validate_evidence` (`scripts/benchmark_assessment_models.py`); only the model varies. One fixed synthetic exercise (demo passage, 7 units) and **12 synthetic ESL answers**: excellent, broken grammar with correct meaning, Vietnamese-pattern grammar, synonyms without keywords, partial, wrong date, contradiction, off-topic, fragments, nothing.
* **Gold labels are author-written and unreviewed**; several are debatable. Treat agreement as a ranking aid, not accuracy. No real learner audio, no transcription noise.
* Run 1: 7 models x 12 x 3 reps (252 calls, no row detail saved). Run 2: 4 models x 12 x 3 reps with per-fixture rows saved. Latency is from the owner's machine to OpenAI (not Render), provider-side only, so absolute numbers differ from production; the ordering is the signal.
* Estimated spend (token usage x price table): run 1 $0.214, run 2 $0.182, smoke test $0.012 = **about $0.41 of the $2 cap**. Check the OpenAI dashboard for the real figure. Prices: gpt-4.1-mini/nano published; gpt-5.4-mini $0.75/$4.50 and gpt-5.4-nano $0.20/$1.25 per 1M tokens from OpenAI model pages (table column labelled "Batch" there, so confirm the standard rate); gpt-5/5-mini/5-nano from third-party pages; the run's table used higher "assumed" values for 5.4, so spend is overestimated.

## Results (36 calls per model per run)
| Model | Run 1 p50 / p90 (s) | Run 2 p50 / p90 (s) | Gold agreement (run 1 / 2) | False "misunderstood" units (1 / 2) | Est. cost per call |
|---|---|---|---|---|---|
| **gpt-4.1-mini (current)** | 2.79 / 3.38 | 2.80 / 3.83 | 0.825 / 0.833 | 0 / 0 | ~$0.0010 |
| gpt-5.4-mini (no reasoning) | **1.90 / 2.50** | **2.28 / 2.72** | 0.853 / 0.869 | 3 / 2 | ~$0.0022 |
| gpt-5.4-nano | 2.83 / 3.48 | 2.87 / 3.45 | 0.786 / 0.837 | 0 / 0 | ~$0.0006 |
| gpt-4.1-nano | 1.99 / 2.72 | — | 0.647 | 0 | ~$0.0002 |
| gpt-4o-mini | 3.37 / 4.96 | — | 0.726 | 1 | ~$0.0004 |
| gpt-5-mini (minimal) | 3.43 / 4.70 | 3.87 / 4.86 | 0.833 / 0.813 | 1 / 2 | ~$0.0011 |
| gpt-5-nano (minimal) | 3.70 / 5.60 | — | 0.746 | 0 | ~$0.0002 |

All 252 + 144 calls returned valid, validated structures. Measured prompt size is ~1,200 input tokens and output ~280-400 tokens for every model (current model: 1,198 in / 294 out), so the prompt is small and latency differences are model speed, not verbosity. Cost per call uses those token counts.

## Findings
1. **Only gpt-5.4-mini is materially faster**: about 0.5-0.9 s lower p50 (-18% to -32%) and better p90, on both runs. Its agreement with the gold labels is equal or slightly higher. It costs about 2x the current model per assessment (still a fraction of a cent).
2. **gpt-4.1-nano is fast but clearly worse** (agreement 0.65, 23 over-credited units). gpt-4o-mini, gpt-5-mini and gpt-5-nano are slower than today's model. gpt-5.4-nano is not faster.
3. **Material disagreements for gpt-5.4-mini (the candidate):**
   * *Fragments* ("App is broken. They fix. Friday."): in 1 of 3 runs it marked **all seven units demonstrated**; in the others it credited three vocabulary/inference units. Gold: only the main idea partly. This is the worst failure seen, an over-credit on almost no evidence.
   * *Wrong date* (learner says Wednesday): it called "postponed" and the inference misunderstood/partial, where gold has them demonstrated. Arguably defensible; the learner's contradictory date makes the context ambiguous.
   * *Misunderstood answer*: it labelled absent details "misunderstood" more than "insufficient"; the current model has the same tendency, and the gold here is itself debatable.
4. **The current model is not clean either**: on the same fragment answer it marked units *misunderstood* (should be insufficient evidence) in 2 of 3 runs, and it over-credits "rolled back" from "They fix". Every model varies run to run: output differs across 3 repeats for 6-7 of 12 fixtures.
5. **Poor grammar with correct meaning is handled well by all leading models**: "broken_grammar_correct" and "vietnamese_pattern" were fully credited by 4.1-mini and 5.4-mini (one 4.1-mini repeat and one 5.4-nano repeat downgraded a unit or two).

## Recommendation
Do not change the model on this evidence: it is 12 answers with unreviewed gold, no transcription noise and no real learners. gpt-5.4-mini is the only candidate that buys real latency (about 0.5-0.9 s of a ~7-9 s turn) and is worth a larger, human-reviewed evaluation, with extra attention to short or fragmentary answers because the app must treat missing evidence as unknown, not as credit. Estimated cost for such a follow-up: well under $1 for ~300 calls per model.

Reproduce (spends money; cap enforced by the script): `.venv/bin/python scripts/benchmark_assessment_models.py --env-file <env> --out <file> --models gpt-4.1-mini,gpt-5.4-mini --reps 3 --budget 0.60`
