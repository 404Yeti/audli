# Library launch plan: 20 curated lessons (supersedes the 60-lesson plan for now)

Goal: a new learner signs in, sees useful listening lessons, chooses one and starts learning without generating anything. C1/C2, extra accents and admin dashboards are deferred. Nothing here has been generated or published.

## 1. Launch set: 20 lessons (A1 5, A2 6, B1 6, B2 3)
Everyday English, travel, workplace, social conversation and short stories. "Pilot" marks the first five. All voices are already approved; accents are varied but quality and usefulness come first.

| # | Level | Title | Topic | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|---|
| 1 | A1 | Hello, my name is… | everyday | Conversation | Canadian | |
| 2 | A1 | Ordering a drink at a café | everyday | Conversation | American | **pilot** |
| 3 | A1 | My daily routine | everyday | Story | Filipino | |
| 4 | A1 | Which bus goes to the station? | travel | Conversation | British | |
| 5 | A1 | The lost umbrella | stories | Story | Australian | |
| 6 | A2 | Ordering coffee at the office | workplace | Conversation | Japanese | **pilot** (acceptance Test 2, if it passes) |
| 7 | A2 | The surprise birthday | stories | Story | Canadian | **pilot** |
| 8 | A2 | Directions to the museum | travel | Conversation | Irish | |
| 9 | A2 | Making a doctor's appointment | everyday | Conversation | Southern US | |
| 10 | A2 | Checking in at the airport | travel | Conversation | New Zealand | |
| 11 | A2 | Meeting a new colleague | workplace | Conversation | Chinese | |
| 12 | B1 | Planning a weekend trip | travel | Conversation | Scottish | **pilot** (acceptance Test 1, if it passes) |
| 13 | B1 | Moving a meeting | workplace | Mixed accents | Indian + American | **pilot** |
| 14 | B1 | Train delays and refunds | travel | Conversation | Filipino | |
| 15 | B1 | A hike that went wrong | stories | Story | Southern US | |
| 16 | B1 | Working from home | workplace | Conversation | Canadian | |
| 17 | B1 | Preparing for a job interview | workplace | Story | British | |
| 18 | B2 | Remote or in the office? | workplace | Conversation | Australian | |
| 19 | B2 | Handling a customer complaint | workplace | Mixed accents | American + Filipino | |
| 20 | B2 | The history of a neighbourhood market | culture | Story | Irish | |

Mix: 12 conversations, 6 stories, 2 mixed-accent lessons; 12 of the 15 accents appear; no repeated scenario. The other three (New York, Boston, Nigerian) join later batches.

## 2. Pilot: five lessons, then stop and review
Pilot = #2, #6, #7, #12, #13. #6 and #12 are the two acceptance lessons the owner is already generating (about $0.20, already authorized), so if they pass acceptance they cost nothing more. The three new ones cost about $0.30 (about $0.10 each).

Pace under the **existing** limits: the ordinary cap is 2 lessons per account per day, so the pilot completes over about two more days after the acceptance lessons. No extra accounts are used. Approving the curator allowance below (5 per day) would finish it in one day; that is a decision for the owner.

Pipeline per lesson (all operator-side, after migration 014 is approved and applied):
1. Generate as the curator through Create Lesson (reviewed spec, existing budgets and guards).
2. Editorial review (below) in the normal lesson player, as the owner, with the transcript on.
3. `register --category` -> `approve --reviewer "<name>" --note` -> `publish`.
4. Second account: lesson visible in Discover, starts without generating, full journey completes. Third account: nothing from drafts is visible.
5. Any failure: do not publish; `unpublish` if already live; record the defect in Linear.

## 3. Editorial quality checks (every lesson, every one must pass)
1. **Language fits the CEFR level**: vocabulary, sentence length and grammar for the level (`check_level_fit` passes; a human confirms it reads naturally). Length: A1 about 1 min, A2 about 1.5, B1 about 2, B2 about 2.5.
2. **Natural, understandable speech**: listened end to end; no clipped words, odd pauses, mispronounced key words or caricatured accent; both voices steady and clearly different; volume consistent.
3. **Correct speakers**: each line is spoken by the speaker it is labelled with; names and gender cues match the voices; accent matches the title.
4. **Transcript sync**: highlight follows the audio from start to end, including seek and replay; no drift.
5. **Four exercises work**: multiple choice (exactly one defensible answer, plausible distractors), spoken response (answerable from the audio; key points reasonable), fill in the blank (accepted answers cover reasonable variants), sequencing (one unambiguous order).
6. **Answers and explanations are accurate**, appear only after answering, and explain the audio, not general knowledge.
7. **Completion and debrief**: the lesson completes, results and debrief make sense, and the learner can return to Discover.
8. **Content hygiene**: no personal data, brands used negatively, stereotypes, politics or sensitive topics; scenario is realistic and useful.
9. **Metadata**: title, level, category and accents correct; shows under the right filters.
10. Reviewer name and note recorded at `approve`; a changed lesson must be re-approved (the digest enforces this).

## 4. Delivery sequence
1. Merge #147 (done), #148 (done), #150 (hardening), #149 (into #138), #151 (browsing), the runbook/plan docs PR.
2. Owner approves **applying migration 014** (runbook: `docs/LISTENING-LIBRARY-MIGRATION-014.md`).
3. Pilot of five; owner reviews; fix defects.
4. Remaining 15 lessons in batches, each reviewed before publishing; no bulk synthesis.
5. Learner-facing success check: a brand-new account sees the published lessons in Discover, filters by level, topic, accent and format, and starts one immediately.

## 5. Curator generation allowance: design only (not implemented, not active)
Request: up to **5 lessons per day for curator accounts**, ordinary learners stay at **2**, every other limit unchanged.
* **Config** (needs owner approval to set in production; default keeps today's behaviour): `AUDLI_LISTENING_GEN_CURATOR_DAILY` (integer, default equal to the ordinary cap, ceiling 5) and the existing curator account list. The generator and API must both see the same list, which is a production configuration change.
* **Code**: `Ledger` replaces the single `per_learner` value with `cap_for(user_id)`: the curator cap only for listed accounts, the ordinary cap for everyone else. Both places that enforce it (`availability` and the reservation inside the transaction) use it. Nothing else changes.
* **Unchanged by design**: the $5/day generation ceiling, the monthly provider-credit allowance, the $2/day assessment guard, the global kill switch/halt, job concurrency and leases, per-request reservations and refunds. A curator at 5/day still cannot exceed any global ceiling; the global limits are checked first and win.
* **Audit**: each curator-allowance lesson is an ordinary `listening_generation_requests` row (user, time, spec, cost), so the ledger already records it. The generator additionally logs one structured line per request counted against the curator allowance (account prefix, day, count of cap), so a day's curator use is greppable and appears in the ops health summary.
* **Tests (when built)**: ordinary learner capped at 2 with the list set; a listed account allowed 5 and refused at the 6th; global ceiling refuses a listed account even under 5; list empty or unset = identical to today; account not on the list cannot use it by any request field; concurrency: simultaneous requests cannot pass the cap.
* **Not done**: no code, no configuration, no extra accounts. Waiting for owner approval.

## 6. Decisions needed
1. Merge #149 (into #138), #150 and #151 once CI is green.
2. Approve applying migration 014 (after reading the runbook).
3. Pilot pacing: accept about 2 days under the 2/day cap, or approve building and enabling the 5/day curator allowance.
4. Confirm the 20-lesson list and the pilot five.
