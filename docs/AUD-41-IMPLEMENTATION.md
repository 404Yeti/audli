# AUD-41 — Listening Progress & Review Hub

Owner approved the migration-free scope on 2026-10-15. Plan stays unchanged; this supersedes the audit's original Plan-history proposal. Implementation is based on main `de04a34` in an isolated worktree, not the unrelated dirty development branch. No production deployment or migration is authorized by this PR.

## Shipped behavior

Review uses the four supplied Figma frames (`59:2`, `59:27`, `59:53`, `59:78`) as its information architecture, adapted to existing Audli CSS/tokens and responsive layout. The source frames clip their content into 50px containers; production uses document flow rather than reproducing that clipping. No new static assets, animation system, analytics storage or provider calls are needed.

`GET /api/review` returns typed practice totals, timezone provenance, paginated completed lessons, separately labeled assessed practice from unfinished lessons, and deterministic qualitative patterns. `GET /api/review/{kind}/{id}` returns a lesson or earlier standalone-session report with its assessed passages. These reuse existing persistence; `/api/history`, transcript gates and original-audio GETs remain intact.

Completed structured lessons count once regardless of passage/follow-up count. Unlinked completed legacy sessions count once as earlier listening practice; the summary explicitly discloses that breakdown. Linked passage sessions never count again, including in unfinished lessons. Missing dates stay unknown and do not contribute practice days. Records without detailed assessment metadata remain in history.

There is no saved learner timezone in the current model. The client supplies its known IANA browser timezone; the server validates it with `zoneinfo`. Missing/invalid zones explicitly fall back to UTC. Calendar dates come from completion instants in the current display timezone, never recording or exercise creation dates. UTC database columns returned naive by SQLite are normalized as UTC; naive/malformed JSON historical dates remain unknown. Historical travel-local dates cannot be recovered.

Reports distinguish demonstrated, partial, incorrect and not-demonstrated meaning from final unit judgments and persisted response evidence. V0.1 remains reportable but does not enter cross-lesson patterns. No flattened feedback list is promoted to structured evidence. Suggested actions use deterministic dimension-specific copy; uncertainty recommends gathering evidence rather than asserting weakness. Original passage accent is shown only when recorded, with fallback context; preferences are never used to infer an accent.

Transcript expansion calls the existing owned, evaluated-attempt-gated endpoint. Original playback fetches retained audio through the authenticated GET endpoint into a disposable blob URL; leaving the report pauses playback, aborts requests and revokes the URL. Missing audio stays unavailable. There is no generation, transcription, assessment or TTS call in Review.

## Pattern policy: review-patterns-v1

Use the latest ten completed lessons (including explicitly labeled standalone legacy sessions). Each lesson contributes at most one final observation per existing dimension: main ideas, supporting details, words in context, implied meaning. Intermediate checkpoints and follow-ups are not counted separately.

Only complete V0.2 unit sets with the same recorded difficulty tuple (speech rate, target duration, vocabulary level, density), recorded passage accent and fallback state qualify. Mixed-context lessons and missing routing metadata do not qualify. Any unknown unit makes that lesson/dimension unknown; otherwise incorrect takes precedence over partial, then demonstrated. Require at least three fully observed lessons on two practice dates within the same context.

All three most recent qualifying observations demonstrated gives “Consistent strength”; at least two partial/incorrect gives “Needs more practice”; other sufficiently evidenced mixtures give “Developing”. Otherwise “Not enough evidence”. The most recent qualifying context is selected deterministically; observation count, context and date window are expandable. These describe recurring saved assessments, not calibrated skill improvement. No average score, pooled V0.1/V0.2 trend or numerical skill percentage is exposed.

## Ownership and performance

Review runs inside the existing request-scoped authenticated repository identity and includes ownership predicates on lessons, sessions, links, exercises, profile and audio asset existence. It neither invokes planner reconciliation nor writes learner state. Responses inherit no-store headers. Foreign detail/transcript/audio IDs return 404; missing/invalid credentials return 401. Frontend lifetimes abort late reads and clear report/transcript/audio state on navigation or account changes.

The dashboard makes four batch reads for a nonempty inventory, independent of page length: scalar lesson inventory, unlinked legacy sessions, final assessed passage snapshots for the requested page/latest ten/unfinished lesson, and profile. It never selects audio blobs in list/report queries. Overview responses exclude scripts, response quotes and numeric assessments. Completion metadata is scanned per learner to derive portable exact local-day counts; expensive passage interpretation is bounded to the selected page/recent pattern window. History pagination uses the last stable kind/ID, avoiding duplicates if newer lessons arrive. No index or cache schema change is introduced.

## Verification and release gate

Automated coverage includes lesson/session deduplication, recording/follow-up exclusion, completion dates, timezone offsets/DST/UTC fallback, absent dates/metadata/results/audio, stable pagination, final-only evidence, pattern thresholds/context separation/latest-ten window, V0.1 compatibility without pooled trends, account isolation, auth rejection, transcript gates, constant query count and provider-free reads. Browser coverage includes overview/report/back navigation, transcript toggle, retained/missing audio, empty/limited/error/retry/loading states, account changes, focus restoration and 320/390/1280px overflow checks. Existing voice, planner and check-in tests remain required.

All eight required CI checks must pass: Backend, PostgreSQL integration, Frontend, Browser, Security, Rollback schema 003, Rollback schema 004, CI required. The rollback jobs exercise the existing certified schema-compatible release; AUD-41 adds no schema change. Final check results and PR link are recorded in Linear and the implementation report after execution. Local synthetic/mock tests establish behavior and isolation, not actual evaluator accuracy or production historical completeness.

## Remaining limits

Finer subskills, historical timezone snapshots, calibrated progress measurement and evaluator/rubric provenance require future structured persistence/design. This release deliberately shows neutral/limited evidence when those facts are unavailable. It does not retrieve production learner content to fill gaps and does not reconstruct lost original audio. The existing transcript endpoint may reject genuinely malformed historical exercise content; Review does not weaken its validation or gate. No production data or schema changes are made.
