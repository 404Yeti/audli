# Audli 2.0 Milestone 03: product experience

Stacked on Milestone 02 ([LISTENING-M02.md](LISTENING-M02.md)). No new service, no migration, no new table: everything is derived from the
attempts, answers and spoken rows that already exist.

## Learner experience
* **Library** (`/listening`): every published lesson with its title, CEFR level, rounded listening time, question count and the learner's
  **real status**: *New*, *In progress · 2 of 4 answered*, or *Finished · 3 of 3 correct* (plus the spoken answer's own state: being checked, checked,
  couldn't be checked, skipped). Easier lessons first. Two lessons ship: A2 *At the pharmacy* and B1 *Ordering food at a restaurant*.
* **Saved sessions**: leaving a lesson never loses anything. *Continue* reopens the same attempt straight at the first open question (the
  listening intro is skipped once any answer is saved). A second tab, a refresh, a double click or a retry with a new request id returns the same
  attempt; answers are final and replay-safe. *See results* opens the saved summary. The only way to start over is the explicit **Listen again**
  on a finished summary, and it keeps the finished attempt in the history.
* **Progress and recent activity**: lessons finished out of lessons available, scored questions correct across finished attempts, and the latest
  eight attempts, newest first. These are counts of what happened. There is **no streak, level, XP, mastery or proficiency** anywhere (tests fail
  if such words or fields appear), and spoken answers are explicitly not scored.
* **States**: labelled loading placeholder, empty library, error with *Try again* (progress failing alone does not take the library down),
  pending / failed / skipped spoken answers, and completion, all in the existing Figma tokens, Fredoka/Nunito type, mascot and 44 px touch targets.
* **Navigation**: library → lesson → four questions → summary → *Back to lessons*; the × in a lesson returns to the library, the × in the
  library returns to Audli home.

## API
`GET /api/listening/lessons` now also carries, per learner, `status`, `attempt_id`, `answered`, `total`, `correct`, `scored`, `spoken_status`,
`last_activity_at`, `completed_count`. `GET /api/listening/progress` returns the totals and `recent`. Both are scoped to the verified account.
Neither contains answer keys, transcripts, rubrics or storage details.

## Verified / not verified
Verified: backend tests on SQLite and PostgreSQL (library, resume, isolation between accounts, progress honesty, failed spoken state);
13 + 4 node tests; the whole journey and library in real Chromium against the real API and the real worker process (axe scans, 44 px
targets, no horizontal scroll at 390/320/1280 px, keyboard path). WebKit runs only in CI. Not verified: real devices (iPhone Safari), screens
compared pixel-by-pixel with dedicated Figma frames, and everything listed as unverified in LISTENING-M02.md.
