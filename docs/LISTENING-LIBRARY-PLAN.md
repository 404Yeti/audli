# Curated listening library: audit, design and plan (AUD-119)

Status: the **foundation is implemented** (PR for AUD-119: migration 014, `app/listening/library.py`, tests). Nothing is applied to production, no lesson has been generated for the library, and no budget or configuration changes. The remaining slices below are planned, not built.

## 1. What exists today (audit)
* **Shared vs private is one rule:** a lesson with **no row in `listening_lesson_owners`** is shared (visible to every learner and the signed-out catalog); a row makes it private to that learner (`store._visible`). The two checked-in lessons are shared; every AI-generated lesson is private to the learner who asked.
* **Immutable versions:** `listening_lesson_versions` (status `draft|published`, content hash); attempts pin a version. A generated lesson is published as a version in the same transaction as its owner row, so it is playable by its owner immediately.
* **Audio:** one content-addressed object per variant (`lessons/...` in the lesson bucket); rows in `listening_audio_variants` point at it. Signed URLs are issued only after an ownership/visibility check.
* **Keys and rubrics:** multiple-choice keys in `listening_exercise_keys`; spoken key points, accepted blanks and the correct order in `listening_exercise_rubrics`. Neither is in any learner response before the learner answers.
* **Generation:** interpret and generate jobs in the separate generator process (own DB role and storage key), a per-learner daily cap (2), a separate daily USD ledger ($5) and a monthly credit allowance (100,000), all unchanged.
* **Roles:** the API's runtime role has SELECT/INSERT/UPDATE on the 2.0 tables and **no DELETE on `listening_lesson_owners`**. That is why publishing is not an API operation (below).
* **Account deletion:** learner rows cascade from `users`; a private lesson is erased with its owner (the durable erasure design is in #138, still unmerged and off).

## 2. Design: how a private lesson becomes a shared one safely
Visibility is not changed. The library adds a **workflow record** and one operator action that moves a lesson across the existing line.

```
generate (private to the curator) -> register (draft) -> approve (named reviewer, exact version) -> publish (owner row removed) -> unpublish (owner restored)
```
* **No audio is copied.** Publishing deletes one owner row; the shared lesson keeps the same audio rows and object key. A test asserts identical audio rows, lesson and version counts before and after.
* **Drafts and unpublished lessons are private by construction** (they are owned), so ordinary learners cannot see, play, attempt or list them, and cannot reach any library route: there is none (a test asserts no `/api/listening` path mentions the library and the API never imports the module).
* **Unreviewed content cannot be published:** publish requires state `approved`, the **same version** that was approved, a fresh readiness check (transcript, audio aligned to every sentence, all four exercise kinds each with a key or rubric) and a named reviewer (`CHECK` constraints enforce reviewer and timestamps in the database).
* **Operator-only, least privilege:** `python -m app.listening.library status|review|register|approve|publish|unpublish` runs with database-owner credentials. The API role keeps its current grants, so a compromised API cannot publish. No production configuration is needed to leave the feature off: the API never reads the new table and does not require migration 014 at startup.
* **Account deletion:** a published lesson has no owner, so deleting the curator's account does not delete it (`curator_id` becomes NULL; `unpublish` then needs `--to-user`). Drafts and unpublished lessons are erased with their curator. Learners' attempts on a lesson that is later unpublished are kept but the lesson answers like an unknown one.
* **Revising:** versions are immutable, so a revision is a new generated lesson, reviewed and published; the old one is unpublished. Approval never carries to a newer version.
* **Categories:** `everyday`, `workplace`, `travel`, `culture`, `stories` (validated in code, so adding one needs no migration).

## 3. Migration requirements
Migration 014 (`docs/postgres-014.sql`, generated like 009-012): one additive table `listening_library_entries`, RLS enabled, revoked from `anon`/`authenticated`, requires 009-012, independent of 013 (account erasure). Recorded in `listening_migrations`, applied only by explicit operator opt-in. **Not applied to production.** Until it is, nothing changes; the operator CLI is the only code that needs it.

## 4. Can lessons be generated sequentially by an admin?
* **Yes, with the existing pipeline and no code change:** the curator uses Create Lesson (all 15 accents, all three formats), reviews the spec, and the generator runs one job at a time per request. One curator generating sequentially keeps Render memory where it is (about 273 MB of 512 MB with one job).
* **Constraint found: the per-learner cap (2 lessons/day).** A single curator account can therefore make 2 lessons a day: the 5-lesson pilot takes 3 days; 60 lessons would take 30. Raising it for a curator needs a new bounded "curation allowance" and **your approval**; I have not changed it. Options: (a) accept the pace, (b) a curator-only daily allowance enforced in the same ledger (still bounded by the $5/day and 100,000 credit ceilings), (c) several named curator accounts (each still capped).
* **Budget:** about $0.10 per lesson (your acceptance estimate; the real credit cost per lesson will be read from the ledger after the two acceptance lessons). 5 pilot lessons about $0.50; 60 lessons about $6, inside the $5/day ceiling if spread over two days and a small fraction of the monthly allowance. ElevenLabs quota is not a constraint at this size.
* **Never bulk:** nothing here generates in bulk, and no code path does.

## 5. Planned slices (each a small, reviewable PR)
1. **Foundation (this PR):** migration 014, workflow module and operator CLI, tests. Reviewed, not applied.
2. **Read-only admin API + review page** (next): list drafts, show transcript, audio and exercises *with keys* to an allow-listed curator only (separate response models, never the learner models), reusing the lesson player for audio. Default off.
3. **Learner library browsing** using the Figma design system: cards, category and CEFR and accent filters on the existing catalog, `category` joined from the library table once 014 is applied. *Not started: the Figma frames for library cards and filters still need to be inspected before any UI is built.*
4. **Pilot** (5 lessons, below), then fixes, then scale in batches of 10 after each batch is reviewed.

## 6. Pilot (5 lessons, about $0.50, nothing published without your review)
Chosen to cover levels, all three formats, original and newly activated voices:
A1 *Ordering a drink at a café* (American dialogue, original voices) · A2 *Meeting a new colleague* (Chinese dialogue) · B1 *Moving a meeting* (Indian + American mixed) · B2 *The history of a neighbourhood market* (Irish story) · C1 *A radio interview about local music* (Boston dialogue, the weakest-sounding voice candidate).
For each: generate as the curator, listen end to end, check transcript sync and all four exercises, try the spoken answer, then `register` → `approve --reviewer` → `publish`, and confirm a second account sees it and a third cannot see a draft.

## 7. Content plan: 60 lessons
Ten per level. Every accent appears 4 to 5 times (at least 3 as the primary accent); 28 dialogues, 25 stories, 7 mixed-accent. Topics are everyday, workplace, travel, culture and stories with no repeated scenario; accents are assigned to spread coverage, never to make the topic depend on the accent, and no lesson asks for an exaggerated or stereotyped delivery. Higher levels use more complex, abstract scripts rather than faster or stronger accents. All titles below are working titles, reviewed before generation.

### A1

| # | Title | Category | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|
| 1 | Hello, my name is… | everyday | Dialogue | Canadian |  |
| 2 | My family and my home | everyday | Story | Irish |  |
| 3 | Ordering a drink at a café | everyday | Dialogue | American | **pilot** |
| 4 | Which bus goes to the station? | travel | Dialogue | British |  |
| 5 | My daily routine | everyday | Story | Filipino |  |
| 6 | Booking a room for one night | travel | Dialogue | New Zealand |  |
| 7 | My first day: where do I sit? | workplace | Dialogue | Indian |  |
| 8 | The lost umbrella | stories | Story | Australian |  |
| 9 | Favourite foods around the world | culture | Story | Nigerian |  |
| 10 | How much is this? | everyday | Dialogue | Japanese |  |

### A2

| # | Title | Category | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|
| 11 | Making a doctor's appointment | everyday | Dialogue | Southern US |  |
| 12 | Weekend plans with a friend | everyday | Dialogue | Scottish |  |
| 13 | Checking in at the airport | travel | Dialogue | Boston |  |
| 14 | Meeting a new colleague | workplace | Dialogue | Chinese | **pilot** |
| 15 | A rainy day in the city | stories | Story | New York |  |
| 16 | A festival in my country | culture | Story | Indian |  |
| 17 | Ordering coffee at the office | workplace | Dialogue | Japanese |  |
| 18 | Directions to the museum | travel | Dialogue | Irish |  |
| 19 | The surprise birthday | stories | Story | Canadian |  |
| 20 | Choosing a gift | everyday | Mixed-accent | American + British |  |

### B1

| # | Title | Category | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|
| 21 | Planning a weekend trip | travel | Dialogue | Scottish |  |
| 22 | A problem with a delivery | everyday | Dialogue | New Zealand |  |
| 23 | Preparing for a job interview | workplace | Story | Australian |  |
| 24 | Moving a meeting | workplace | Mixed-accent | Indian + American | **pilot** |
| 25 | Train delays and refunds | travel | Dialogue | Filipino |  |
| 26 | Why street food is loved | culture | Story | Boston |  |
| 27 | A hike that went wrong | stories | Story | Southern US |  |
| 28 | Moving to a new flat | everyday | Dialogue | Chinese |  |
| 29 | Working from home | workplace | Dialogue | Canadian |  |
| 30 | A local tradition explained | culture | Story | Nigerian |  |

### B2

| # | Title | Category | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|
| 31 | Negotiating a deadline | workplace | Dialogue | British |  |
| 32 | Getting around a big city | travel | Story | New York |  |
| 33 | The history of a neighbourhood market | culture | Story | Irish | **pilot** |
| 34 | Handling a customer complaint | workplace | Mixed-accent | American + Filipino |  |
| 35 | An unexpected detour | stories | Story | Scottish |  |
| 36 | Renting a car abroad | travel | Dialogue | Japanese |  |
| 37 | Remote or in the office? | workplace | Dialogue | Australian |  |
| 38 | Volunteering abroad | culture | Story | New Zealand |  |
| 39 | Neighbours and noise | everyday | Dialogue | Southern US |  |
| 40 | The art of small talk | culture | Mixed-accent | Nigerian + British |  |

### C1

| # | Title | Category | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|
| 41 | Presenting quarterly results | workplace | Story | Indian |  |
| 42 | Slow travel and its trade-offs | travel | Story | Chinese |  |
| 43 | A radio interview about local music | culture | Dialogue | Boston | **pilot** |
| 44 | A difficult client call | workplace | Dialogue | New York |  |
| 45 | The last ferry | stories | Story | Irish |  |
| 46 | How languages change | culture | Story | Canadian |  |
| 47 | Panel: planning a greener city | culture | Mixed-accent | Scottish + Australian |  |
| 48 | Booking an event venue | workplace | Dialogue | New Zealand |  |
| 49 | A story told at the family table | stories | Story | Filipino |  |
| 50 | Reading the travel insurance small print | travel | Dialogue | Japanese |  |

### C2

| # | Title | Category | Format | Accent(s) | Pilot |
|---|---|---|---|---|---|
| 51 | The ethics of AI at work | workplace | Dialogue | American |  |
| 52 | A lecture on migration and identity | culture | Story | Nigerian |  |
| 53 | An after-dinner speech | stories | Story | British |  |
| 54 | Negotiating a partnership | workplace | Mixed-accent | Southern US + Indian |  |
| 55 | Documentary: the future of cities | culture | Story | Australian |  |
| 56 | The long way home | stories | Story | New Zealand |  |
| 57 | A heated but friendly debate | everyday | Dialogue | Chinese |  |
| 58 | Reflections on a year abroad | travel | Story | Canadian |  |
| 59 | Humour across cultures | culture | Mixed-accent | New York + Irish |  |
| 60 | A board-level briefing | workplace | Dialogue | Boston |  |

## 8. Safe rollout sequence
1. Merge the foundation (nothing applied). 2. Your acceptance of the 15-accent library (in progress). 3. Approve applying migration 014 to production. 4. Pilot of 5 over three days. 5. Review, fix, then batches of 10 with a review after each. 6. Admin API and review page; library browsing UI once the Figma frames are checked. 7. Each lesson goes live only through an explicit named approval.

## 9. Risks and open decisions
* Per-learner cap vs. curation pace (needs your decision, section 4).
* Account deletion (#138) is unmerged; the design above is compatible with it (published lessons are not owned) but the combination is untested until both land.
* The review step is human: the readiness check proves structure, not that the audio sounds right.
