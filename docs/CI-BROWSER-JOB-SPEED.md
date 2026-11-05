# Why the 1.x "Browser" CI job takes about 29 minutes, and how to cut it without cutting coverage

Measured from one completed run (main push, `.github/workflows/ci.yml`, job `Browser`), step durations from the GitHub Actions API. No job was started to produce this.

| Step | Seconds | Share |
|---|---|---|
| Mocked browser regressions (`npm run test:browser`, 407 tests, `workers: 1` in CI, one `next dev --webpack` server) | **930** | 53% |
| WebKit iPhone classroom and native codec acceptance | 286 | 16% |
| Native HTTP/audio acceptance with synthetic evidence and local espeak | 188 | 11% |
| Short spoken choices through real HTTP, Chromium and WebKit | 138 | 8% |
| Recording recovery, Chromium and iPhone WebKit | 95 | 5% |
| Assessment recovery correctness | 64 | 4% |
| Setup (container init, `pip install`, `apt`, `npm ci`, blocked-greeting recovery) | ~85 | 5% |
Total about 1,750 s. **Everything runs one after another on one runner, and the biggest step is a single-worker run of 407 tests.** The other jobs finish in 1-16 minutes, so this job alone sets the wall-clock time of every PR, and PostgreSQL integration (13-16 min) is next.

## Proposal: run the same steps in parallel jobs and keep the names branch protection requires
Nothing is deleted, skipped or loosened; every test still runs, in the same Playwright image, with the same commands. Only the grouping changes.

| New job | Steps (unchanged commands) | Estimated runtime |
|---|---|---|
| `Browser shard 1/3`, `2/3`, `3/3` | `npx playwright test --workers=1 --shard=N/3` on the default config | ~310 s test + ~60 s setup = **~6 min each** |
| `Browser WebKit recovery` | classroom-safari, upload-recovery, recovery-correctness, playback-recovery configs | 286+95+64+21 = 466 s + setup = **~9 min** (split in two for ~5 min if wanted) |
| `Browser real HTTP` | lesson-media (native HTTP/audio), short-choice configs | 326 s + setup + espeak = **~7 min** |
| **`Browser`** (aggregate, same name as today) | `needs:` all of the above, `if: always()`, passes only if every one succeeded | seconds |

Wall-clock for the browser stage falls from ~29 to **~9 minutes** (about -70%); total runner time rises ~15% because each job repeats ~1 minute of setup. The slowest remaining job becomes PostgreSQL integration (13-16 min), which can be split the same way (by test file) in a second step.

## Why this does not weaken protection or bypass anything
* **Branch protection is untouched.** It requires the check names `Backend`, `PostgreSQL integration`, `Rollback schema 003`, `Rollback schema 004`, `Frontend`, `Browser`, `Security`, `CI required`. The aggregate job keeps the exact name `Browser`, exactly like `CI required` already aggregates the others; `CI required` adds the new Browser jobs to its `needs`. No admin override, no changed rule.
* **Coverage cannot silently shrink.** Add a guard in the aggregate: each shard records `npx playwright test --list --shard=N/3 | tail -1`; the aggregate sums them and fails unless the total equals `npx playwright test --list` for the whole suite (407 today). A test dropped by sharding, a typo in a shard count, or a new config left out of every group fails the build.
* **Same tests, same environment, same flags.** `--workers=1` and the pinned Playwright image stay. Shards are Playwright's own deterministic split. No test is retried or marked flaky to save time.
* **Failure artifacts** keep their current names per job (`browser-failure-<job>`).

## What I would not do
Raise `workers` above 1 on the mocked suite (it was set to 1 deliberately and the suite shares one dev server); switch the tests from `next dev` to a production build (changes what is tested); cache or skip the cold dev-server compile (small, and hides regressions); drop WebKit; or make any check optional.

## Expected effects and risks
* Faster feedback on every PR, and shorter stacked-PR merge sequences like the one just completed (each retarget-and-retest cycle was ~30 min).
* More concurrent runners per PR (7 jobs instead of 4). If runner concurrency is limited on the plan, the gain shrinks; check before adopting.
* The guard compares counts, not names; if a rename is expected, update nothing: the count is computed, not committed.
* Implementation is one workflow edit plus the guard, validated by `actionlint` locally and by the PR's own run (this one run is the cost of the change). It is not included in this change.
