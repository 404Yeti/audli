# AUD-77 Review v2 quality tuning (after live evaluation 1)

**Status: code and prompts changed, flag still off, migration 007 still unapplied, no paid call made for this work.** Evaluation 1 (2026-10-23, `gpt-4.1-mini-2025-04-14`, prompt v2, 55 synthetic fixtures, 99 requests, $0.0209) found 0 observed unsafe acceptances, 8/46 false rejections and edits to already-natural answers. This document records what was changed, what an **offline replay** proves, and what only a **new live evaluation** can prove.

## 1. The eight false rejections, individually

Original = the learner's (synthetic) answer. "Expected" = the fixture's reference. Layer = which layer rejected it in evaluation 1.

| id | original | saved candidate | layer / code | expected | root cause | change | verified how |
|---|---|---|---|---|---|---|---|
| h15 | I am very boring in this class because the lesson is slow | I am very bored in this class … | 4 `meaning_check_failed` | bored | comparison model read boring→bored as a change of sense (a person who is boring vs feels bored) | comparison prompt: a word form that selects the sense the sentence clearly intends is a correction | **needs live** |
| h33 | She is the most tallest girl in our class | She is the tallest girl in our class. | 3 `quantity_changed` | tallest | `most` counted as the quantifier MOST; its removal looked like a quantity change (it would then also have tripped `comparison_changed`) | a `more`/`most` directly before an already-comparative/superlative word is a doubled marker and is ignored by the quantity and comparison checks | offline: passes layers 1-3; **layer 4 needs live** |
| h35 | I am almost sure he comes to the party | I am almost sure he will come to the party. | 3 `modality_changed` | will come | adding `will` is a modality change | `agreement_will`: exactly this repair only (see §2) | offline: passes layers 1-3; **layer 4 needs live** |
| h39 | The road was closed, so we take another way | The road was closed, so we took another way. | 4 `meaning_check_failed` | took | comparison model treated a tense fix as different (the saved response gives no reasoning) | comparison prompt names tense/agreement consistent with the sentence's own time reference as a correction | **needs live** |
| h42 | We visited Lisbon last summer and we like it much | We visited Lisbon last summer and we liked it a lot. | 2 `content_added` | liked it a lot | two bugs: `like` is a stopword but `liked` was not, and `come`/`comes` stemmed differently from each other (`com`/`come`) | `liked/likes/liking` are stopwords; stems strip a final `e` consistently | offline: passes layers 1-3; **layer 4 needs live** |
| h44 | Paris is the capital of Germany and it have many museums | Paris is the capital of Germany and it has many museums | 4 `meaning_check_failed` | has | the candidate is exactly the grammar fix and keeps the false statement. The comparison model still said "different"; nothing in the saved response says why. Most likely it judged the false claim rather than the change | comparison prompt: judge what each text *says*, never whether it is true; a wrong statement must remain the same wrong statement | **needs live** |
| h45 | Water boil at fifty degrees so we make tea fast | Water boils at fifty degrees, so we make tea quickly. | 4 `meaning_check_failed` | boils | same as h44, plus the coach also swapped `fast`→`quickly`, an unneeded edit | comparison prompt as h44; coaching prompt now forbids swapping an acceptable word | **needs live** |
| h47 | He is thirty years old and he was born in two thousand ten | He is thirty years old and was born in two thousand ten. | 4 `meaning_check_failed` | (unchanged) | **Not a pipeline defect.** The fixture's reference equals the source ("wording is already acceptable"), so the right result is "unchanged". The coach dropped an optional pronoun, which is an unneeded edit, and the check rejected it. It is scored `false_rejection` only because the fixture's `expect` is `accept` rather than `accept_unchanged` | coaching prompt: leave an optional subject or pronoun alone | **needs live**; fixture label to be corrected in the new set (the old one is not edited, to keep its hash) |

h44 and h45 are the "preserve the learner's intended statement" cases. In both, the saved candidate is correct: it fixes agreement only and does not touch Germany, Paris or fifty. Deterministic layers 1-3 accept it and also **reject** every factual correction (Berlin, France, one hundred degrees; tested). So the safeguard against correcting facts is intact and independent of the comparison model; the defect is the comparison model rejecting a safe candidate.

## 2. Deterministic changes (all in `app/improvement.py`, each narrow)

1. **Doubled comparison marker** (`undoubled`): `more`/`most` directly before a word in `COMPARATIVE_FORMS` is dropped before the quantity and comparison checks. "most tallest"→"tallest" passes. Still rejected: →"shortest" or →"tall", "more taller"→"bigger"/"shorter", "Most students passed"→"Students passed"/"All students passed" (a genuine quantifier is untouched).
2. **One `will` repair** (`agreement_will`): the only accepted modality change is `will` replacing an inflected verb with its own base form ("comes"→"will come"), when the source has no modal, no negation and no habitual word (always, every, usually, never …). Still rejected: must/can/would/should/might, "will not", a past verb ("came"→"will come"), a different verb, a habit ("He always comes"), a source modal ("might"→"will"), a negated contraction ("doesn't"→"won't").
3. **Stopword/stem consistency**: `liked/likes/liking` join `like`; the stemmer strips a final `e` for words longer than 3, so inflections of one verb share a stem. Content added/removed is still counted: "liked it"→"hated it", a new place, or new content is still `content_added`.
4. **Cosmetic-only suggestions** (`cosmetic_only`, `natural_already`, used in `coach`): when a suggestion differs only in punctuation, capitalization or a regional spelling (a closed table of ~70 US/UK pairs, never a pattern), the learner's own text is returned as "already natural": no correction, no note, no comparison call, no repair, nothing from the candidate kept. The provider's own self-report must be clean (`meaning_preserved`, not `adds_information`). `can not`/`cannot`, `do not`/`don't`, `desert`/`dessert`, number words and negation are **not** cosmetic.
5. `PROMPT_VERSION` v2→v3. Learner retry accounting is per prompt version, so earlier rejections do not count against v3.

No safeguard was removed or disabled. Layer 4 is still consulted for every real edit, and its "different" always rejects.

## 3. Prompt changes (`app/services/openai_provider.py`; effect not provable offline)

- **Coaching**: edit a word only when it is wrong in the sentence (tense, agreement, article, preposition, plural, word form or collocation); do not edit an acceptable answer; explicitly leave alone British/American spelling and vocabulary, optional punctuation and commas, an optional subject or pronoun, contractions, and a correct word you would merely have chosen differently; keep a false statement exactly as said and fix only the English.
- **Independent comparison**: judge the claim each text makes, never its truth; lists the corrections that leave the claim unchanged (with the h15/h39/h44/h45/h47 patterns as examples) and the changes that do not (negation, number, name, quantity, certainty/modality, cause/effect, comparison, opinion, detail, who did what, a corrected fact); "if you cannot tell, answer false" is kept.

The tests pin this wording. Whether the models behave as instructed can only be shown by new provider calls.

## 4. Offline replay (`scripts/replay_review_improvement_eval.py`)

Re-runs the **saved** provider responses from evaluation 1 through the current layers 1-3 and the cosmetic-only rule. No network, no key. A saved layer-4 verdict is reused only for the same candidate; a candidate that previously never reached layer 4 is `needs_live_check`, never counted as accepted.

| classification | evaluation 1 | replay | note |
|---|---|---|---|
| accepted_ok | 37 | 37 | |
| false_rejection | 8 | **5** | h33, h35, h42 left this class |
| needs_live_check | 0 | **3** | h33, h35, h42 now reach layer 4; no saved verdict |
| unneeded_edit_accepted | 4 | **1** | h16 (comma), h18 (traveling), h20 (period) became unchanged_ok |
| unchanged_ok | 0 | **3** | |
| unneeded_edit_rejected | 1 | 1 | h17 |
| rejected_poor_candidate | 1 | 1 | h29 |
| blocked_ok | 4 | 4 | |
| FALSE_ACCEPT | 0 | **0** | |

**Offline proves**: the deterministic rule changes behave as intended on these inputs, introduce no unsafe acceptance on them, and cannot let the adversarial pairs in tests through (each loosened rule was mutation-tested: weakening it fails a test).

**Offline does NOT prove**: anything about h15, h39, h44, h45, h47 (layer 4), h17 and h19 (prompt behaviour), the three `needs_live_check` cases' layer-4 verdicts, or any effect of the new prompts. Those need provider calls, and the 55 fixtures have now been used for tuning, so they are no longer held out.

## 5. Proposed release-quality thresholds (for the next evaluation; additional to zero observed unsafe acceptances)

Sample size is stated with every number; a small set cannot prove a low rate, so the 95% Wilson upper bound is reported and part of the rule.

| measure | definition | release requires |
|---|---|---|
| Unsafe acceptances | accepted candidate that breaks a fixture invariant or a seeded meaning change | **0** (blocks). Report the rule-of-three bound: 0/N ⇒ <3/N at 95% |
| Seeded unsafe corrections | ≥24 hand-made meaning-changing candidates (negation, number, name, quantity, modality, cause, comparison, fact "correction") sent through layers 1-4 | **100% rejected** (layers 1-3 offline, layer 4 live) |
| False-rejection rate | good candidate rejected ÷ fix cases with a good candidate (n ≥ 70) | point estimate **≤ 10%** and Wilson upper bound **≤ 20%** |
| Unnecessary-edit rate | natural answers (n ≥ 30) shown a correction ÷ natural answers; cosmetic-only counts as unchanged | point estimate **≤ 10%** and upper bound **≤ 20%** |
| Correction yield | fix cases accepted and correct ÷ fix cases | **≥ 80%** |
| Accepted-correction quality | mean best F1 of accepted corrections | **≥ 0.95** |
| Provider reliability | failed ÷ requests, no retries | **≤ 2%**, no auth or protocol errors |
| Latency | per improvement | p95 **≤ 5 s**; maximum reported with its call |
| Cost | per attempt | mean **≤ $0.001**; whole run under the approved cap |

Any miss means: investigate the failures individually and do not recommend activation.

## 6. Proposed next evaluation plan (requires fresh owner authorization; nothing below has been run)

1. **Fresh held-out set**, written before any run and frozen by hash: ~100 cases (≥70 fix cases across the existing categories; ≥30 natural answers covering regional spelling, optional commas, optional pronouns, acceptable synonyms, contractions, and false statements that must be kept), and the h47 label corrected to `accept_unchanged`. The current 55 become the regression set (the replay and tests above).
2. **Seeded set**: ≥24 meaning-changing candidates and ≥12 same-meaning pairs (including the five layer-4 patterns) sent to the comparison step alone, so layer 4 is measured directly and cheaply. The runner needs a small extension for this (a separate PR; not part of this one).
3. **Stage 1 (layer-4 calibration)**: only the seeded pairs, about 36 calls (cents). Continue only if all seeded unsafe pairs are rejected.
4. **Stage 2**: the full fresh set, pinned model, prompt v3, no retries, same $1 hard cap; expected cost a few cents, worst-case reservation near the cap, so `max_requests` is set from the new plan.
5. Report against §5, investigate every miss individually, then (only if it passes) prepare the migration/release checklist. The 007 apply tooling still has to be written and rehearsed separately.

Not done and not done here: applying migration 007, enabling `AUDLI_REVIEW_IMPROVEMENT_ENABLED`, any paid call, any change to older backups.
