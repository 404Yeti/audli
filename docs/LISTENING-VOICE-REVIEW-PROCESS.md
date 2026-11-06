# How your voice-review decisions are applied (AUD-116)

Nothing is approved by default, and a successful synthesis never approves anything. A voice becomes the speaker 2 for same-accent dialogue only when you write **approve** for it.

## 1. Listen and decide (you)
Open `C:\Users\Lenovo\projects\audli\docs\listening-accent-review\index.html` in a browser (double-click it). For each of the 12 accents it plays your speaker-1 voice next to the
proposed speaker 2 (all 12 are real Flash 2.5 samples of the same line). Choose **Approve**, **Reject** or **Unsure** per accent. Press **Copy my decisions** and paste the text here.
Your choices are kept in the browser, so you can listen over several sessions. Skip any accent you have not decided: it stays disabled.

## 2. What the text can say (one line per accent)
| You write | Result |
|---|---|
| `Irish: approve (Darragh ... BOt7zZh6gzfWlIUYnyPz)` or just `Irish: approve` | the proposed voice is approved |
| `Japanese: approve 1MPZGta71qtdTm2BpYUl` | an **alternate** (any candidate id listed in the matrix) is approved instead |
| `Chinese: reject` or `Chinese: reject mBoVD3461U2BagYEwjeo` | that candidate is rejected: never approved, never re-proposed; the next listed candidate becomes the proposal and is reviewed next round |
| `Boston: unsure` / `Boston: no decision` / not mentioned | nothing changes |
Partial approvals are normal. An id that is not a listed candidate, a voice you already rejected, or the speaker-1 voice is refused and **nothing** is changed.

## 3. Applying it (me)
1. Save your text to a file and run `python -m scripts.apply_voice_review decisions.txt` (dry run: prints exactly what would change). Then the same with `--write`.
2. The tool edits only the header line of each decided accent in `app/accent_catalog.py` (`approved` / `rejected`); every voice id and label stays byte-identical; it re-validates the catalog.
3. Run the per-accent tests (two distinct voices in speaker order, both speakers in the accent, transcript sync, all four exercises, nothing else changes) and open a PR. No provider is called; no cost.
4. After CI is green and you OK the merge, production deploys. Because the formats flag and the 12 accents are already set in Render, each approved accent **appears for same-accent dialogue immediately after the deploy**; I check `/api/health` shows it under `conversation`.
5. Optional controlled acceptance: one real dialogue per approved accent (about US$0.10 each, within the existing limits; needs a signed-in account and your approval of the spend).

## Rolling back one accent
Set its `approved` back to `None` (or run the tool with a `reject` line) and deploy: that accent returns to monologue and mixed-accent only; lessons already made keep playing.

## Unchanged
Speaker-1 voices (your Audli 1.x selections), the original three accents' pairs, mixed-accent and monologue for all 15, production budgets and the worker architecture.
