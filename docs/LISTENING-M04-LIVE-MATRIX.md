# Live validation matrix for the Audli 2.0 models (APPROVED and RUN 2026-11-04: see LISTENING-M04-LIVE-RESULTS.md)

Approved by the owner in chat on 2026-11-04 with a cumulative ceiling of US$5.00 and a dedicated key; the approval is recorded in `docs/listening-live-approval.json`, bound to the
exact matrix hash. **It was run on 2026-11-04 for about US$0.20; results and caveats are in `docs/LISTENING-M04-LIVE-RESULTS.md`.** The approval no longer matches the case file (four expectations were corrected afterwards), so any further run needs a new approval.
Budget requested: **ceiling US$5.00** (the hard maximum the harness accepts). Worst-case estimate of everything below: **US$1.65**; expected: about US$0.40.

## What it will run (`docs/listening-live-matrix.json` + `docs/listening-live-cases.json`)
| Row | Task | Model / path | Calls | Worst case | Question it answers |
|---|---|---|---|---|---|
| A1 | assess | `gpt-6-luna`, Responses, strict schema, default reasoning | 90 (30 cases × 3) | US$0.13 | Does the candidate default meet the criteria? How consistent is it? |
| A2 | assess | `gpt-6-luna`, `reasoning.effort = none` | 30 | US$0.02 | Is the parameter accepted, and does it change quality/cost? |
| A3 | assess | `gpt-6.1-terra` | 30 | US$0.76 | Only matters if luna fails |
| A4 | assess | `gpt-4.1-mini`, Chat Completions | 30 | US$0.06 | Does the rollback path still work before the 2027-01-14 shutdown? |
| T1 | transcribe | `gpt-transcribe` (`languages=['en']`) | 18 clips | US$0.04 | Text quality, `languages` field, empty-`languages` on noise/silence/tone |
| T2 | transcribe | `gpt-4o-mini-transcribe` (baseline) | 18 | US$0.03 | Comparison only |
| G1 | generate | `gpt-6.1-terra` | 12 (6 prompts × 2) | US$0.56 | Valid `LessonContent` and level fit? |
| G2 | generate | `gpt-6-luna` | 12 | US$0.06 | Same, cheaper |
Total 240 calls. Worst-case numbers are estimates × 1.5 and include assumed hidden reasoning tokens; the harness replaces each reservation with the
usage the provider reports, so the results file shows measured spend.

**Cases.** 30 assessment cases (2 lessons × 5 categories × 3): *correct*, *incorrect* (confident wrong facts), *incomplete* (one point only), *ambiguous* (vague, pronoun-only),
*unintelligible* (hesitation noises, "I did not hear it", off-topic). Each lists which verdict states are acceptable per key point; incorrect, ambiguous and unintelligible cases may
**never** be credited as `covered`. up to 18 audio clips built locally for free (`--make-audio` currently makes 16: 12 espeak-ng speech clips from the correct/incorrect cases, plus white noise, pink noise,
silence and a tone). Human recordings (at least two voices, one non-native accent) can be added to the manifest; they are not part of the cost above.

## Pass criteria (decided before seeing results)
* Zero verdicts that credit understanding without a verifiable quote reaching a learner (the domain layer guarantees it; the matrix reports how often the model *tried*: `ungrounded_claims_downgraded`).
* `correct` cases: ≥ 80% pass. `incorrect`, `ambiguous`, `unintelligible`: 0 `false_covered`, ≥ 90% pass. `incomplete`: ≥ 80%.
* Malformed/unusable responses ≤ 2%. Same case, 3 repeats: ≥ 90% identical verdicts (`inconsistent_cases`).
* Latency p95 under 20 s per assessment; end-to-end answer under 30 s.
* T1: ≥ 80% of expected keywords on speech clips; noise/silence/tone clips reported as unreliable or under 3 words in ≥ 90% (this is the invented-transcript test).
* G1/G2: ≥ 90% valid `LessonContent`, ≥ 70% level fit. (Generation is informational: nothing in 2.0 calls it yet.)
A failed row is reported as a table with the failing cases, never averaged away. The model defaults change only if the table supports it.

## How the harness protects the budget
`--run` starts with a **pre-flight**: a free `models.retrieve` for every model id, then one real call per row. If a model, endpoint or parameter is refused (auth, unknown model, bad request) the run stops, writes a report and exits with status 2 without running the matrix, so a wrong configuration costs cents. Spend is cumulative: earlier reports in the results folder count against the approved ceiling, including reruns after a compatibility fix.

Run with no flags it prints the plan and the matrix hash and makes **no call**. `--run` refuses unless an approval file names that exact hash (so editing the matrix or cases voids
the approval), a ceiling of at most US$5, an approver, and an unexpired date, and unless the worst-case total fits under the ceiling. It reserves each call's worst case before sending it,
stops (and says so) before a call that could pass the ceiling, never prints or writes the key, and writes only verdicts, counts and costs. It uses the production adapters, so a pass validates
the code path the worker will run. Tests (`tests/test_listening_live_trial.py`) prove every refusal against a mocked transport.

## To approve and run (you)
```bash
.venv/bin/python -m scripts.listening_live_trial                                   # read the plan and note the sha256
.venv/bin/python -m scripts.listening_live_trial --make-audio /tmp/audli-audio     # free, local
cat > approval.json <<'JSON'
{"matrix_sha256": "<sha256 printed above>", "max_usd": 5.0, "approved_by": "<you>", "approved_at": "<ISO time>", "expires_at": "<ISO time, e.g. +24h>"}
JSON
OPENAI_API_KEY="$(cat ~/.audli-trial-key)" .venv/bin/python -m scripts.listening_live_trial --run --approval docs/listening-live-approval.json --audio-dir /tmp/audli-audio
# ~/.audli-trial-key: the dedicated key, mode 600, created in your own terminal (never pasted into a chat or committed)
```
Results land in `docs/listening-live-results/` (git-ignored). Use a dedicated OpenAI project key with its own spend limit as a second, provider-side ceiling.

## Not covered by this matrix (separately gated)
Lesson-audio synthesis (ElevenLabs or other), real-device recording, real S3, production data. Each needs its own approval.
