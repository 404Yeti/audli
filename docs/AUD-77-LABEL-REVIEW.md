# AUD-77 evaluation 2: independent blind label review (2026-10-23)

Nothing here ran a provider or read a credential. Raw reviewer output, mapped back to fixture ids, is in `docs/AUD-77-LABEL-REVIEW-DATA.json`.

## Method
- Two reviewers per dataset (held-out, calibration), four reviewers in all, each a fresh AI session with no access to the repository code, the author's labels, the reference rewrites, the invariants, the validator rules or **any model output**. They received only shuffled items with opaque ids (`heldA-017`), in a different order for each reviewer, and decided from their own knowledge of English.
- Held-out items (the 100 that reach the provider): `natural` (a fluent teacher would not correct it; regional and informal speech is natural; a false fact is still natural), `fix` (with a minimal correction), or `ambiguous` (apparent error, intent undeterminable, any correction would be a guess), plus confidence and a reason.
- Calibration pairs: does the proposed text assert exactly the same thing (`same`) or not (`different`), plus confidence and a reason.
- Limits that remain: **the reviewers are AI sessions of the same model family as the author**, so their errors can correlate with mine (for example a shared blind spot about a regional usage). This is a stronger check than the author's own pass, not a substitute for a human reviewer. A human spot-check of the items listed under "Concerns" is still recommended.

## Results
**Calibration (39 pairs): complete agreement.** Both reviewers and the author label the same 27 pairs `different` and the same 12 pairs `same`. This includes the four pairs that only the AI meaning check can catch (c19 invented cause, c20 cheap→expensive, c26 a person dropped, c27 caller and callee swapped). Reviewer low-confidence flags: c04, s06, s09 (below).

**Held-out (100 items): all three labelers agree on 97.**
- All **32 natural answers** are `natural` for both reviewers (UK and US variants, optional subjects, contractions, fluent false facts, natural meaning-sensitive sentences).
- 63 of the 64 fix cases are `fix` for both reviewers. Across all 127 reviewer corrections (63 + 64), every one satisfies the author's invariants and required fixes (nothing preserved is dropped, nothing forbidden is added), so the references and invariants are not stricter than what expert teachers produce.
- Ambiguous: a02 and a03 unanimous. Three disputes:

| id | text | author | reviewer A | reviewer B |
|---|---|---|---|---|
| f54 | This road is more safe than the old one | fix ("safer") | **natural** ("grammatical though less usual") | fix |
| a01 | My friend told me that she go to the station | ambiguous | **fix** ("goes", low confidence) | **fix** ("goes", low confidence) |
| a04 | My sister and her friend she bought it yesterday | ambiguous | ambiguous | **fix** (low confidence: "redundant pronoun") |

## Disputes and resolutions (made before any approval; recorded here so nothing is altered silently)
| id | resolution | why |
|---|---|---|
| f54 | **Replaced** by "This box is more heavier than that one" (fix: "heavier") | "more safe" is accepted usage, so the case was an unreliable test of false rejections. The replacement is a double comparative; two fresh blind reviewers labelled it `fix` with high confidence and the same correction |
| a01 | **Withdrawn** | both reviewers read it as an ordinary agreement error, against the author's "ambiguous". Rather than keep a contested label, the case was removed (it is not re-labelled `fix`, which would make 65 fix cases and break the ratified count of 64) |
| a05 (new) | **Added** to keep four ambiguous cases: "Tomorrow I saw the doctor about my knee" (past tense clashes with "tomorrow"; "Tomorrow I will see…" and "Yesterday I saw…" are different claims) | two fresh blind reviewers: `ambiguous`, high confidence, both naming those two readings |
| a04 | **Kept as ambiguous** (author and reviewer A against reviewer B); disclosed | ambiguous-intent cases are reported separately and never counted in the unnecessary-edit gate |

The held-out file changed once, from `91bc9d23…dffd` (merged in #78) to **`30513a0de1533726f6615d3d089753f784ac2e37b4ac680d6aeda04f4bbc24e5`**. Counts are unchanged: 104 cases = 64 fix + 32 natural + 4 ambiguous + 4 blocked. The calibration file is unchanged (`00eb8d51fb309c1d2e410af9e676b5f1a204df20922a7d096c314b16568fde2f`). Both hashes are pinned by tests and bound by the approval file. These are the final frozen versions; any further label change would need a new hash, a new approval file and a new entry in this document.

## Concerns to be aware of
1. **Reviewers are not human** (see Method). If you want a human check, the items worth the time are the ambiguous cases (a02, a03, a04, a05), the informal natural answers (n13-n18) and the regional ones (n01-n10).
2. **c04** ("They don't have no time" → "They have time"): both reviewers say `different` (negation removed), reviewer A with low confidence because some dialects read the double negative as a single negation. The label stays `different`, the conservative reading: a correction that deletes a negation must not be accepted.
3. **s06** ("she comes tonight" → "she will come tonight") and **s09** ("many informations" → "a lot of information"): labelled `same` by both reviewers, reviewer A with low confidence (present-for-future and quantifier synonymy are judgments). These pairs are diagnostic only; a rejection of either by the comparison step is reported as informational and is not a gate.
4. The four pairs that pass layers 1-3 (c19, c20, c26, c27) depend on the comparison step alone; the review confirms they are genuinely meaning-changing, so a leak there is a real failure of the design, not a labelling artifact.
5. The calibration set bounds the unsafe leak rate only below about 10.5% at 95% (0 of 27). The review improves confidence in the labels, not in the sample size.
