# 15-accent library: production acceptance (AUD-108 / AUD-111)

Status: **authenticated tests NOT yet performed.** Revision `d313e2d` (PR #146) is live; `/api/health.listening_formats` lists 15 accents under `monologue`, `conversation` and `mixed_accent_conversation`. Everything below the line "Authenticated" needs a signed-in learner (the owner). Budget authorized for acceptance: two lessons, about $0.20 estimated, hard limit $0.30; no quota reset, no spending-guard bypass, no configuration change, no further generation.

## Read-only checks (no sign-in)
- [x] `/api/health`: `enabled: true`; 15 accents in each of the three formats; Welsh and South African absent; no voice ids in the response.
- [x] Render deploy live on `d313e2d`; no error or warning logs; memory about 273 MB of 512 MB.
- [x] `/`, `/listening`, `/listening/create`, `/classic` return 200; the availability endpoint returns 401 when signed out.
- [ ] After the lessons below: logs, ledger, R2 and jobs review (engineering verification at the end).

## Authenticated (owner)
Quota: 2 lessons per learner per day; these two tests use all of it. Do not double-click Generate or refresh while a job runs.

### A. Create Lesson UI (no cost)
- [ ] Story: all 15 accents selectable (American, British, Australian, Scottish, Indian, Irish, Canadian, New Zealand, Chinese, Japanese, Filipino, Nigerian, Southern US, New York, Boston).
- [ ] Dialogue: the same 15, none marked "coming soon".
- [ ] Mixed accent: all 15 for speaker 1 and all 15 for speaker 2; the same accent twice is not offered.
- [ ] Welsh and South African are not offered.
- [ ] "A Scottish conversation about booking a train" in plain language gives a Scottish dialogue in the review step (never swapped).

### B. Test 1: Scottish, B1, same-accent dialogue, two friends planning a weekend trip
Speaker 1 Chris (`csXxiUN2BUFflsCaDxPM`), speaker 2 Isla Skye (`TVmbglAk3F1GkiCoOq47`).
### C. Test 2: Japanese-accented English, A2, same-accent dialogue, two colleagues ordering coffee
Speaker 1 Yuki (`DiHqGqDdN8tWkUxlsPeg`), speaker 2 Tatsuya Watanabe (`69Na567Zr0bPvmBYuGdc`).

For each lesson:
- [ ] Two audibly different voices; each line spoken by the speaker it is labelled with; named accent held steadily (note Chris is a casual voice; Japanese was the weakest candidate).
- [ ] Transcript highlighting follows the audio, including after seeking and replay.
- [ ] Multiple choice, spoken response (recording accepted, assessment returns), fill in the blank and sequencing all work; answers and explanations appear only after answering.
- [ ] Debrief appears and the lesson completes.
- [ ] Exactly one new lesson in the library; no error banner, stuck job or retry loop.

### D. Existing lessons
- [ ] Two or three older lessons (American, British or Australian, an older generated one) play, transcript matches, exercises work. `/classic` loads.

## Report back
Per lesson: accent, title, start time, pass/fail per item, any error text or screenshot.

## Engineering verification (after the owner reports)
Generation logs; the generation ledger and provider spend (one job and one charge per lesson, about $0.10 each, credits equal ready plus failed-but-charged); R2 lesson audio keys and recording cleanup under `listening-tmp/`; Render memory and worker restarts; failed or duplicate jobs; API and frontend errors. Confirmed issues go to Linear with narrow fixes.

AUD-103, AUD-104, AUD-106, AUD-107, AUD-108 and AUD-111 stay open until their own acceptance requirements are met.
