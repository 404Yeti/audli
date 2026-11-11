# AUD-21: isolated compact evidence A/B experiment

## Publication, availability and deferred work

AUD-21 latency optimization is paused in favor of onboarding and beta readiness.
The compact evaluator is **rejected**. Recommendations below are historical
research proposals, not authorization to implement or adopt an optimization.
This documentation-only branch introduces no evaluator or production code.
See the [TTS feasibility report](AUD-21-TTS-FEASIBILITY.md) for the later study
and the [application flow](APPLICATION_FLOW.md) for the accepted product behavior.

The 600-assessment results and all per-workload findings are preserved below.
The scripts `scripts/benchmark_compact_evidence.py` and
`scripts/analyze_compact_evidence.py`, fixtures under `app/experiments/`, and
`tests/test_compact_evidence.py` remain unpublished local research artifacts in
the original Audli workspace. **They are not included in this PR and are not
available from a fresh clone of this branch.** No separate code archive or
published research-code branch currently exists.

The ignored dataset `data/compact-evidence-ab-20261009/` and the excluded pilot
`data/compact-evidence-pilot-20261009/` remain local only. The final dataset has
`samples.jsonl`, `blind-review.jsonl`, `report.json`, `analysis.json`, and
`SHA256SUMS.json`. Its four covered artifact hashes were verified before
publication. The expanded review records contain authored synthetic workloads,
not learner recordings or transcripts. Neither dataset is uploaded in this PR.

The reproduction commands below describe the original local study. To reproduce,
first obtain the unpublished scripts/fixtures and synthetic archive from the
workspace owner, verify its checksum manifest, use the existing development
requirements, privately configure credentials, and choose a new output folder.
Do not run the commands in a fresh checkout without those prerequisites. The
report is complete evidence of the recorded findings; missing public artifacts
limit independent reproduction and are explicitly acknowledged.

## Scope and existing critical path

The production `OpenAIProvider.assess` makes one GPT-4.1 mini Responses API call,
using structured `ListeningEvidenceAssessment` output. Each required comprehension
unit has a fixed key (`dimension_index`). Grounded statuses are demonstrated,
partially demonstrated, or misunderstood; their evidence is a closed enum of exact
learner excerpts. Unknown units require an empty excerpt and a clarification
question. Feedback, transcription concern, and concern reason are also required.

Excerpt candidates include sentence/chunk excerpts and complete short turns.
The schema and payload contain these candidates, and the response repeats an
excerpt for each grounded unit. One long sentence can be repeated for several
units. Historical microphone telemetry has no assessment token counts; actual
usage is measured by this provider experiment rather than reconstructed.

Application code validates the assessment, handles STT uncertainty, chooses any
clarification, calculates comprehension and deterministic adaptation, and persists
before coaching. Coaching text is assembled from authoritative assessment and
adaptation, never spoken directly from evaluator notes. Coaching TTS preparation
already overlaps acknowledgment playback; audible playback remains serial. The
experiment times provider assessment and local schema/resolution/validation,
not database persistence, TTS, microphone capture, or browser playback.

The user's latest microphone baseline is approximately 10.28 seconds per exercise
response, including approximately 3.94 seconds assessment/response preparation and
2.61 seconds coaching audio preparation. Those are a different workload and
measurement boundary from this synthetic provider benchmark.

## Candidate and security boundaries

The candidate is offline-only, disabled by default, and never imported by API
routes. Running its CLI requires `AUDLI_COMPACT_EVIDENCE_EXPERIMENT=1`, development
settings, OpenAI, and the unchanged `gpt-4.1-mini` model. It accepts only the built-in
synthetic fixtures and never opens the learner database or writes learner state.
Enabling the flag does not switch the application's production assessment path.

Each request snapshots the authorized learner excerpts, exercise, and required
unit keys. A fresh random request scope is echoed and checked. IDs are qualified
by unit, such as `U3:E2`, in closed schema enums. Unit qualification prevents a
reference from being reused for another unit, while distinct references allow
one genuine excerpt to support multiple units, preserving existing semantics.
Unknown/foreign-unit/malformed IDs, missing fields, duplicate JSON keys, stale
request scopes, and tampered source maps reject. Unknown units retain empty
references and required questions. Before downstream use, all references resolve
to exact snapshot excerpts and the **unchanged** domain grounding validator runs.
No reference or request scope reaches learner-facing feedback.

A request scope is a correlation/replay check, not a replacement for authentication
or ownership. This experiment has no network endpoint. Any future production
adoption would still require existing request-scoped authentication and ownership.
There is no hidden retry from candidate to baseline: rejection counts as failure.
Production remains on the existing implementation as the safe fallback.

Exact excerpt membership is not proof of semantic relevance to a unit. The
existing domain validator checks membership and structural rules, while evaluation
quality is tested separately. Compact references must not be accepted on the
basis of membership alone.

## Methodology

Ten authored synthetic workloads: correct, partial, poor grammar, incorrect,
missing information, contradiction, uncertainty, clarification, long answer,
and multiple details. All use the same eight-unit exercise, preserving equivalent
inputs between A and B. Thus breadth across topics and variable unit counts remains
a limitation. Workload weights are equal, not measured production traffic weights.

A invokes the **unchanged** `OpenAIProvider.assess`. B uses the compact schema and
resolves into the original `EvidenceAssessment`. Both use the same model, endpoint,
SDK settings (90-second timeout, two SDK retries), instructions except the evidence
representation, `store=False`, and default provider generation settings. No token
limit or model change biases one variant. A separate 20-call pilot checked viability;
it is excluded from final results.

The final run has 30 pairs per workload, 600 calls. Within a pair the calls are
serial; order alternates by workload and pair index. Workloads are round-robin.
Three bounded concurrent pairs keep the run practical. This measures comparative
provider performance under bounded concurrency, not a single-user microphone path.
All timings use one Python monotonic clock. Provider time includes HTTP/SDK parsing
and retry delays; assessment time includes schema creation, response conversion,
reference resolution, and domain validation. It excludes deterministic downstream
processing, artifact writing, and progress reporting. Schema warmup is not omitted.
Repeated synthetic inputs may use provider prompt caching; cached input tokens are
reported. This is independent of Audli's TTS cold/warm cache.

Artifacts under ignored `data/compact-evidence-ab-20261009/`:

- `samples.jsonl`: numeric/enumerated metrics, statuses and deterministic decisions.
- `blind-review.jsonl`: expanded **synthetic** assessments and learner cues, with
  opaque review IDs and no A/B labels, reference IDs, request scopes or credentials.
- `report.json`: intermediate and final per-workload results.
- `analysis.json`: revalidated results, citation screen and paired bootstrap interval.

The analyzer re-runs grounding validation, authored status/uncertainty oracles,
and deterministic downstream decisions against expanded assessments. An additional
fixture-specific authored excerpt-relevance screen detects irrelevant citations,
including superseded evidence for explicit contradictions. It is independent of
candidate format and not a general semantic evaluator. Authored status exactness
is conservative; partial/main-idea/inference judgments can be ambiguous. Human
quality certification is not implied. Blinded review should inspect actual cues
and assessment/unit associations, rather than merely trusting token counts.

p50 is the median; p90 uses nearest rank. Report standard deviation and range.
Paired latency improvement is shown both as the difference of variant medians and
as the distribution of within-pair differences. A deterministic, workload-stratified
paired bootstrap provides an approximate aggregate 95% interval (2,000 resamples).
No conclusion relies on partial-run quantiles or the pilot's small sample count.

Prespecified quality screening tolerances: candidate unit accuracy no more than
2 percentage points below baseline, exact status rate no more than 5 points below,
and uncertainty accuracy no more than 2 points below. These screens do not establish
noninferiority at n=30. Explicit harmful contradiction, feedback or adaptation
regressions fail adoption regardless of aggregate accuracy. The 500 ms latency
gate requires 30 valid pairs and at least 500 ms improvement in median assessment
latency; both aggregate and workload results are reported. Failures, retries and
semantic grounding must be reviewed independently.

Cost estimates use reported input/output usage and cached-input usage at
[$0.40/M input, $0.10/M cached input, $1.60/M output](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
They are estimates, not invoice totals; failed SDK attempt billing can be absent.
Tokens include structured-output schema overhead as reported by the provider.

## Reproduction from WSL

From the repository root with the existing private `.env` configured:

```bash
AUDLI_COMPACT_EVIDENCE_EXPERIMENT=1 .venv/bin/python -m scripts.benchmark_compact_evidence \
  --pairs 30 --workers 3 --output data/compact-evidence-ab-retest
.venv/bin/python -m scripts.analyze_compact_evidence data/compact-evidence-ab-retest
.venv/bin/pytest -q tests/test_compact_evidence.py
```

Use a new output folder each run. Do not paste API keys into commands. No browser,
microphone, frontend or database is needed for this synthetic experiment. It cannot
establish the 8-second milestone or the 5-second end-of-speech target; future accepted
changes would still need equivalent real-microphone workloads and the existing
privacy-safe automatic collector.

## Results and recommendation

## Completed results (9 October 2026)

600 valid assessments, 30 paired samples per workload, with no final provider,
schema, reference-resolution, or literal-grounding failures. The 20-call pilot is
excluded. The final run used three concurrent pairs.

**Recommendation: reject this candidate and retain the existing production path.**
It fails the 500 ms gate, costs more, and materially degrades contradiction handling
and citation relevance. No experimental adoption or production change occurred.

| Aggregate metric | A: existing | B: compact |
|---|---:|---:|
| Provider p50/p90 (s) | 3.332 / 4.174 | 3.141 / 3.990 |
| Assessment p50/p90 (s) | 3.337 / 4.178 | 3.160 / 4.008 |
| Mean input tokens | 1405.30 | 2269.98 |
| Mean output tokens | 308.18 | 269.21 |
| Estimated cost for 300 samples (USD) | 0.288145 | 0.378578 |
| Final failures / SDK retries | 0 / 1 | 0 / 0 |
| Authored unit status accuracy | 95.08% | 93.79% |
| Citation-relevance screen failures (units) | 54 | 467 |

Aggregate median assessment reduction: **176.7 ms**, approximate stratified paired bootstrap 95% interval **91.1–273.6 ms**. The median within-pair reduction is 177.2 ms. Neither interpretation reaches 500 ms.

| Workload (30 pairs each) | A assessment p50 / p90 (s) | B assessment p50 / p90 (s) | Median gain (ms) | A/B status accuracy | Downstream agreement |
|---|---:|---:|---:|---:|---:|
| correct | 3.276 / 3.619 | 2.980 / 3.377 | 295.7 | 100.0% / 100.0% | 100.0% |
| partial | 3.271 / 3.978 | 3.518 / 4.119 | -246.3 | 87.5% / 91.2% | 80.0% |
| poor-grammar | 3.085 / 3.507 | 2.817 / 3.257 | 268.1 | 99.2% / 100.0% | 93.3% |
| incorrect | 3.249 / 3.740 | 3.117 / 3.857 | 131.4 | 100.0% / 100.0% | 100.0% |
| missing-information | 3.327 / 3.932 | 3.448 / 4.129 | -121.3 | 75.0% / 78.8% | 60.0% |
| contradiction | 3.464 / 4.575 | 3.087 / 3.886 | 376.5 | 100.0% / 68.8% | 50.0% |
| uncertainty | 3.503 / 4.532 | 3.627 / 4.407 | -123.9 | 93.3% / 100.0% | 100.0% |
| clarification | 3.443 / 4.145 | 2.981 / 3.701 | 462.5 | 100.0% / 99.2% | 93.3% |
| long-answer | 3.517 / 3.989 | 3.180 / 3.811 | 336.9 | 100.0% / 100.0% | 100.0% |
| multiple-details | 3.489 / 4.622 | 3.076 / 3.622 | 413.1 | 95.8% / 100.0% | 66.7% |

No individual workload passes the 500 ms median reduction gate.

| Assessment variability | A (s) | B (s) |
|---|---:|---:|
| Mean | 3.791 | 3.343 |
| Standard deviation | 5.287 | 0.908 |
| Minimum | 2.535 | 2.290 |
| Maximum | 93.897 | 10.076 |

The baseline incorrect-answer sample, pair 6, had one SDK retry and took 93.893 s
in the provider wrapper. It is retained, not trimmed. The retry count establishes
a retried request, not whether the initial delay came from transport, provider
processing, or another failure. The 90-second timeout is consistent with this
observation, but the experiment did not capture the initial error category. This
single sample inflates means and standard deviation; use the reported medians
and p90s for the latency acceptance gate. Compact's maximum was 10.076 s with no
SDK retry. No inference about the previous microphone outlier is made.

### Tokens, cost and quality

Mean output tokens fell 12.6%, but input tokens increased 61.5%. Estimated total cost increased 31.4%. The per-unit closed reference enums are repeated in separate schema definitions; this increases input overhead despite shorter generated evidence. Cached input totals are A 94,720, B 76,800.

The request-local resolver passed all structural rejection tests and every live
output resolved to a genuine authorized excerpt. That **does not** establish
semantic grounding. The separate authored citation screen found 54 unsupported
unit/excerpt associations in A and 467 in B. These are screening counts rather
than a certified semantic error rate, but spot review of expanded outputs
confirmed concrete wrong-detail assignments in the candidate.

On contradictions, A matched the authored eight-unit oracle in all 30 samples;
B matched it in none. Unit accuracy dropped from 100% to 68.75%, and deterministic
downstream agreement was only 50%. Some compact outputs attached the member-count
excerpt to the hall detail, the start-time contradiction to member count, and the
repair date to start time. Those excerpts were genuine, but assigned to the wrong
units. Some resulting learner cues asked when the session started instead of
correcting the confirmed seven-versus-six misunderstanding. This violates the
quality, evidence-relevance, clarification and feedback acceptance requirements.
The resolver does not shift slots; unit-key/reference-prefix conversion is covered
by equivalence tests. The observed incorrect associations originate in model output.

Clarification exact-status agreement also fell beyond the prespecified 5-point
screen tolerance. Aggregate downstream agreement was 84.33%; it includes follow-up
and blocked decisions, not only final adaptation events. For the 181 pairs where both variants reached final assessment, aggregate scoring
and adaptation signatures agreed in 100% of pairs. This conditional denominator
excludes transitions where one variant requested a follow-up instead. It also
hides incorrect unit attribution within equally weighted details. Expanded final
learner cues matched exactly in only 166/181 pairs (91.71%); the 15 mismatches were
contradiction cases where compact coaching corrected the member count instead of
the actual mistaken start time. Scoring agreement therefore does not establish
coaching quality or correct evidence attribution. Uncertain cases remained
blocked from adaptation in both variants. Both formats sometimes disagree with
conservative authored partial/inference labels; those ambiguous cases must not be
interpreted as certified comprehension accuracy. The clear contradiction regression
is sufficient to reject this candidate without relying on ambiguous cases.

The excerpt-relevance labels and authored status oracles are format-independent.
Unlabeled expanded synthetic outputs were inspected for concrete feedback and
citation defects. No independent human learner-feedback acceptance or general
semantic accuracy study was performed. No adoption claim depends on such evidence.

### Verification and remaining work

- Full backend: **439 passed**, including SQLite persistence and access-control tests.
- Focused experiment tests: **28 passed**. The full suite includes 27; the additional
  final-score/adaptation summary test passed in the subsequent focused run.
- Frontend helpers: **51 passed** across five suites.
- Manual/automatic collectors: **24 passed**. Subprocess JSON tests initially failed
  inside the sandbox and passed unchanged outside it.
- Chromium: **83 passed, 1 skipped**.
- ESLint, TypeScript and isolated production build: passed.
- Python compilation, diff whitespace and new-file credential-pattern scan: passed.
- No PostgreSQL test URL was configured; live PostgreSQL verification was not run.
- Generated `next-env.d.ts` build changes were restored; no frontend source change remains.

Files: `app/experiments/__init__.py`, `compact_evidence.py`, `evidence_fixtures.py`,
`citation_oracle.py`; `scripts/benchmark_compact_evidence.py`,
`scripts/analyze_compact_evidence.py`; `tests/test_compact_evidence.py`; this document.
Numeric and synthetic artifacts remain in ignored `data/`, not Git.

No accepted full-conversation speedup results from this experiment. Even if the
176.7 ms synthetic assessment reduction transferred unchanged, it would explain
only a small portion of the approximately 2.28 s reduction needed to move the
user-reported 10.28 s average toward 8 s. Subtracting these differing workload
measurements is not a valid new microphone result. Neither 8 s nor 5 s is demonstrated.

Next engineering step: retain the current evaluator. If compact references are
revisited, create a separate experimental candidate that preserves original unit
keys and reduces schema/input duplication, beginning with the failed contradiction
and clarification fixtures. Require zero clear unit/citation shifts before another
30-pair-per-workload latency and quality run. Do not deploy a reference format on
output-token savings alone. Existing TTS generation remains another substantial
stage; further audio work requires its own measured design and recovery review.

AUD-21 remains In Progress. AUD-23 is unchanged. No commit, push, merge or deployment.

## Handoff closeout assessment (9 October 2026)

The next session inspected branch `feat/beta-feedback-forms`, HEAD `2467176`,
the entire working-tree status, the original provider/schema and route imports,
the experiment guards, the report and all 600 samples. There are **no tracked
implementation edits to revert** and no unrelated working-tree changes at this
handoff. All pending files belong to this rejected offline experiment.

Retain `app/experiments/`, both compact benchmark/analyzer scripts, their focused
tests and this report as reproducible research artifacts. They are not candidates
for production adoption. The module is never imported by production services or
routes; the standalone benchmark requires its explicit opt-in flag, development
settings and the original model. Setting that flag cannot switch the API evaluator.
Do not wire this module into provider selection, routes, coaching or deployment.
There is no need to delete or move research files to restore the default evaluator.

Retain ignored `data/compact-evidence-ab-20261009/` locally, including expanded
synthetic review outputs; these are not learner records. Re-running the analyzer
revalidated 600 rows and reproduced the 176.699 ms median gain and rejection.
Focused experiment tests passed again: 28. Full-suite closeout results are recorded
in the subsequent TTS feasibility report. Production assessment, coaching, models,
transcript gates and the 1.8-second silence threshold remain unchanged. This
disposition is an assessment and preservation step, not an approval to commit.
