# AUD-41 — Review data audit and proposed implementation

Status: initial audit approved for migration-free implementation on 2026-10-15. **Owner override: Plan remains unchanged**, superseding the audit proposal to move its history UI. No application code, schema, deployment or paid provider calls changed. Linear: [AUD-41](https://linear.app/failed-training/issue/AUD-41/listening-progress-and-review-hub).

## Scope and evidence

Inspected the current workspace at HEAD `2467176ec74b6e056eeeab93de6ddfb1e9043753`, including its extensive existing modified/untracked application files. Findings describe the inspected source and persistence contracts, not a census of production learner records. No production database or private learner content was queried. Historical field completeness and actual audio availability must be evaluated per record; schema support is not proof every historical row has evidence.

Primary sources: `app/models.py`, `app/evaluation.py`, `app/conversation.py`, `app/conversation_api.py`, `app/main.py`, `app/auth.py`, `app/lesson.py`, `app/lesson_api.py`, `app/storage/{schema,repository,adapters,migrations,planner}.py`, `app/planner_api.py`, `scripts/import_sqlite.py`, `docs/PERSISTENCE.md`, `web/app/{page.tsx,globals.css}`, `web/components/{product-shell,adaptive-plan}.tsx`, and existing API/auth/persistence/conversation/lifecycle tests. Read the eight-check workflow from the local `origin/main` Git ref because this checkout has no `.github` directory; that ref was not refreshed during the audit.

### Figma inspection

Inspected all four frames through Figma MCP design context and screenshots in [the supplied file](https://www.figma.com/design/TOyKOA5mrWAWI8WvvS7lvy/Audli-Hands-Free-Experience?node-id=59-2):

| Frame | Node | Intended information architecture |
| --- | --- | --- |
| Review overview | `59:2` | Journey heading, practice summary, grounded insight, conditional trend, recent lessons |
| Lesson details | `59:27` | Back navigation, summary, strengths, practice areas, next focus, evidence explanation, transcript, original replay |
| Listening patterns | `59:53` | Recurring strengths/difficulties, evidence labels, connection to Plan |
| New learner and limited evidence | `59:78` | First-lesson invitation, transcript-only older record, insufficient-pattern evidence |

The frames live on page `17:2`, Product Shell + Flows. Metadata initially listed only the mascot page; read-only Plugin API inspection located the frames. Screenshots show content clipped into 50px containers; inspectable child text supplies the intended sections, not a usable fixed-height production layout. Use existing Inter typography, navy/blue/teal tokens, rounded bordered surfaces, responsive document flow and keyboard controls. Placeholder dates, totals, progress bars and claims are illustrative only. “Group conversations” does not authorize multiple speakers. The requested subtitle takes precedence over the design placeholder.

## Existing Review and assessment behavior

`GET /api/history` delegates to `SQLProgressRepository.history()`: one owned join over attempts, exercises, evaluations and adaptation events, ordered by **attempt creation time** then ID. It returns attempt/exercise IDs, recording timestamp, evaluation JSON and adaptation JSON. It lacks lesson grouping, completion time, content metadata, pagination and audio availability. Inner joins can omit damaged/incomplete historical result records; it must not be the sole source of a complete lesson inventory.

`web/app/page.tsx` fetches history during Product refresh and Review navigation. Review reverses it, shows only five exercise feedback entries, and fetches historical transcripts on demand. There is no lesson report or pattern aggregation. Transcript state is shared with session UI: new navigation must clear/scope it to the selected exercise and account. `AdaptivePlan` currently renders completed lessons as well as upcoming cards. Move the completed-history presentation to Review, retaining planner counts, roadmap accounting, upcoming cards and spoken check-ins.

There are three different counting units:

- **Attempt:** a recording; summary and follow-ups are separate attempts. Only the root attempt receives the final evaluation.
- **Exercise / training session:** `save_exercise()` creates one `training_sessions` row per passage; `complete()` finalizes that session with the final assessment. This is not the structured ten-minute lesson.
- **Structured lesson:** `lesson_lifecycles` groups multiple exercises through `lesson_exercises`. Completion is recorded in the Lesson JSON when the closing phase is acknowledged; paused/active lessons are not completed lessons. A completed lesson can have no assessed exercises, so completion alone must not imply comprehension evidence.

Final V0.2 evaluation is written transactionally with adaptation, profile update, completed training-session time and conversation state. Follow-ups do not produce additional final assessments. Intermediate turn assessments/evidence exist separately; aggregating all checkpoints would double-count and preserve errors that follow-ups resolved. Use final evaluation units only for Review outcomes.

## Field-level inventory

Classes: **A** = already persisted and reliable for valid records; **B** = deterministic derivation; **C** = future structured persistence needed for the requested precision; **D** = currently unsupported. Availability qualifiers apply to older records.

| Field | Source and reliability | Class / Review use |
| --- | --- | --- |
| Learner identity / ownership | `users`, exercise/lesson/session `user_id`; attempts/results/evidence inherit exercise ownership | A. Request-scoped server-verified identity; no browser learner-ID authorization |
| Lesson completion | `lesson_lifecycles.status`, JSON `completed_at`; legacy `training_sessions.completed_at` | A/B. Group structured lessons; timestamp provenance matters |
| Attempt timestamp | `attempts.created_at` | A. Recording time, **not completion date** |
| Historical completion fallback | Persisted adaptation-event time; legacy migration uses event time or recording time if absent | B. Mark estimated/missing timing; migration-created timestamps are not original observations |
| Title / topic | `exercises.content.title/topic`; optional Lesson `plan_topic` | A/B. Planned topic is a label, not proof of passage content; generic fallback for absent metadata |
| Listening level | Content `vocabulary_level`, exercise difficulty snapshot | A. Label as passage/lesson material level, not certified learner CEFR |
| Current listening level | Profile difficulty `vocabulary_level` | A with qualification. “Current practice level”; default profile does not establish assessed proficiency |
| Difficulty and pace | Content and exercise difficulty: rate, duration target, vocabulary level, density | A. Rate is a configured multiplier, not measured words/minute; target duration is not actual listening time |
| Original transcript / expected units | Exercise content `script`, main idea, details, inference, vocabulary | A. Only assessed, owned exercises can expose transcript or answer labels |
| Original learner response | Attempt transcription JSON, recognized text, confidence, uncertainty, source, confirmed text | A. Keep uncertainty/source; never imply transcript is raw audio or exact speech |
| Follow-up questions / answers | Conversation JSON; `conversation_followups`, `conversation_turns`, attempts | A where retained. Older intermediate checkpoints can be absent; no reconstruction |
| Final assessment | `evaluation_results.data`, version and score projections | A. Final root result is authoritative, not the last intermediate checkpoint |
| Demonstrated comprehension | V0.2 units `demonstrated`; V0.1 `understood`; unit dimension/index maps to content via `expected_units()` | A/B. Specific expected meaning plus persisted learner evidence |
| Partial understanding | V0.2 `partially_demonstrated`; V0.1 `partial` | A/B. Read units; flattened `understood`/`missed` lists mix partial labels with other statuses |
| Incorrect understanding | Unit `misunderstood` plus evidence | A. Distinct from omissions |
| Information not demonstrated | V0.2 `insufficient_evidence`; V0.1 `missed` | A with version semantics. Neither proves incorrect understanding; unknown supplies no correction |
| Overall / dimension score | Evaluation JSON/projections, V0.2 nullable scores and coverage | A for that assessment; D for a pooled progress percentage or comparable longitudinal measure |
| Supporting evidence | Final unit `evidence`, dimension/index; normalized turn assessments and evidence rows | A/B where present. V0.2 validates quotes against accepted turns; validity does not establish evaluator accuracy |
| Assessment feedback | Persisted evaluation feedback; V0.2 finalization replaces evaluator notes with terminal feedback | A. Use as saved feedback with provenance; do not infer structured categories from prose |
| Adaptation | Event JSON: decision, focus, reason, policy version, old/new difficulty, changed variable | A. Reuse deterministic recommendation; incomplete evidence can choose focus without proving weakness |
| Lesson closing summary | Lesson `strength`, `improvement_focus`, `closing` | A where present. Derived summary, not additional independent evidence |
| Passage accent | Content `passage_voice` records accent/provider/fallback/validation; lesson has consent/routing checkpoint | A when explicitly recorded. Use actual passage routing; preferences/offers/consent alone do not prove delivered accent |
| Original passage audio | `exercises.audio_name`, owned `audio_assets.data`, filesystem cache | A where bytes retained. Existing GET replay restores cache from DB without synthesis; filename alone is insufficient |
| Learner audio | Validated upload discarded after transcription | D. No recording replay or recovery |
| Completed lesson total | Structured completed lessons plus deduplicated unlinked legacy completed sessions | B. Do not use `completed_attempts` as lesson count |
| Distinct practice days | Completion instants converted to validated display timezone | B. Historical completion-local timezone is C, not recoverable from UTC alone |
| Four broad patterns | Final units in `main_idea`, `details`, `vocabulary`, `inference` | B under conservative thresholds below |
| Times/numbers, intentions, sequence, changed plans | No consistent structured subskill tags | C. Do not classify historical prose with speculative AI or keyword heuristics |
| Improvement / learning gain | Adaptive tasks differ; no calibrated equivalent tasks, consistent assessment model/rubric provenance or independent proficiency measure | D currently. Repeated success is not proof of improvement |
| Total minutes listened | Lesson elapsed wall-clock includes interaction; targets are not playback telemetry | D as actual listening time; omit |

## Historical semantics and limits

V0.1 scores `understood=1`, `partial=.5`, `missed=0`, `misunderstood=0`. V0.2 scores observed evidence only (`demonstrated=1`, partial=.5, misunderstood=0); insufficient evidence is unknown, dimension/overall scores may be null, and coverage is separate. Both use weighted dimensions (.35/.40/.15/.10), but matching weights do not make these results comparable. V0.2 dimension summaries can also mix observed and unknown units: a partial dimension status is not necessarily a confirmed partial answer. Use units.

Passage difficulty, content and accent can change between exercises, including inside a lesson. Do not average mixed lesson levels, combine V0.1/V0.2 scores, chart profile priors as results, or count unresolved/transcription-concern attempts. Even matching difficulty/version does not calibrate tasks or prove longitudinal comparability. Initial scope should omit a numerical chart and show a dated qualitative assessment history with material-level context and a short explanation that tasks vary.

Legacy SQLite upgrade/import preserves available content, results, adaptation, conversation and audio. It does not recreate lost intermediate assessments or audio. Newly introduced projection times can be migration times. New readers should tolerate missing optional metadata, invalid unit references and absent evaluations, retain the history entry, and show “Detailed assessment unavailable” rather than fabricate a report. Gate transcript separately using the existing evaluated-attempt rule.

## Proposed deterministic rules (for owner approval)

### Lesson inventory and practice days

1. One history row per completed structured lesson, with its assessed passages nested in detail. Exclude active/paused structured lessons from the completed total; expose already-assessed passages from them in a separately labeled “Assessed practice” section so existing transcript access survives.
2. Include completed legacy sessions only when their exercises are not linked to any structured lesson. Label them “Earlier listening practice”; include these as one lesson-equivalent each in the practice summary, with that definition visible in help copy. Never double-count a linked exercise/session or follow-up attempt.
3. Use structured `completed_at`; for standalone legacy records prefer the persisted completion/event instant. Never silently promote recording time or migration time to exact completion. Include known completed records with missing dates in totals and an “Date unavailable” group; exclude them from exact practice-day totals and disclose that limitation.
4. Sort dated entries by completion descending, stable ID tie-break; use ascending completion order for any timeline. API cursors must preserve this order.
5. Browser supplies an IANA timezone from `Intl.DateTimeFormat().resolvedOptions().timeZone`; backend validates using `zoneinfo`. Missing/invalid zone falls back to UTC and reports `timezone_used`. Compute distinct calendar dates from completion instants in that zone, including DST correctly. These are days in the **current display timezone**, not known historical travel-local dates. Do not let timezone input affect ownership or daily training eligibility.
6. Show current material level only as “Current practice level”, with a neutral unestablished state before assessment. For multi-level lessons list recorded passage levels rather than invent a single proficiency label.

### Reports and next focus

Resolve final unit dimension/index against that exercise's persisted content. Display separate demonstrated, partial, incorrect and not-demonstrated groups; evidence quotes are plain text. Unknown information never generates a corrective assertion. Use complete bounded action copy from existing focus/tip policy for observed errors/partial answers; when only evidence is missing, recommend another listening opportunity rather than claim a weakness. Persisted adaptation reason can explain stable difficulty. Missing structured units means limited saved-feedback/transcript review, not fabricated evidence.

Expose recorded passage accent only when the actual routing metadata supports it, including fallback. For original replay reuse authenticated `GET /api/exercises/{id}/audio`, with an availability projection based on owned bytes/cache existence; do not select blobs into list queries. A stale availability check may become a 404: show an unavailable state, never call generation/TTS. Keep transcripts behind `GET /api/exercises/{id}/transcript` and check each passage, even inside a completed lesson.

### Listening patterns

Initial taxonomy: main ideas, supporting details, words in context, implied meaning. Do not rename all inference as speaker intentions. No percentages or “getting better” claim.

Proposed conservative rule version `review-patterns-v1`:

- Use final V0.2 units from the latest ten completed structured lessons/standalone legacy sessions; legacy V0.1 remains report-only initially. Deduplicate linked sessions. A lesson contributes at most one observation per dimension, regardless of exercise/follow-up count.
- Partition observations by exact recorded difficulty tuple (rate, target duration, vocabulary level, density) and actual recorded passage accent; missing context and mixed-context lessons do not qualify. Unknown evaluator/rubric provenance remains a limitation: describe these as recurring saved assessment observations, not calibrated skill measurement.
- For a dimension, any insufficient unit means unknown for that lesson. Otherwise any misunderstood unit gives incorrect, otherwise any partial gives partial, otherwise all demonstrated gives demonstrated. Retain these source distinctions in reports.
- Require at least three fully observed lessons on at least two practice dates in the same context. Fewer observations: “Not enough evidence.” Unknowns neither count as failures nor fill the observation threshold.
- All three most recent eligible observations demonstrated: “Consistent strength.” At least two of those three contain incorrect or partial understanding: “Needs more practice.” Other sufficiently evidenced mixtures: “Developing.” Show observation count/context and date window in accessible explanatory text. Labels describe those observations only; they must not imply increasing ability.
- If no eligible repeated evidence exists, show “Complete a few more lessons to discover your listening patterns.” A dashboard insight may repeat a supported pattern, otherwise use that neutral message. Do not generate new prose through a provider.

These thresholds are a conservative product policy proposal, not a validated psychometric scale. Version the pure aggregation policy in code and test exact boundaries. Changing taxonomy or claiming improvement needs a separate evidence design.

## Smallest safe implementation path

No additional persistence or production migration is necessary for the proposed initial hub. Existing JSON and relational records support useful reports, history, practice counts, qualified material levels and conservative broad patterns.

1. **Assessment Data Audit:** owner reviews this document and approves counting, timezone, qualitative history and restricted-pattern scope before application edits.
2. **Review Dashboard:** add typed owned read projections, deterministic lesson inventory/count/day policy and paginated history; render requested heading/subtitle, summary and neutral/evidenced insight. Preserve `/api/history` for compatibility. Remove completed-history UI from Plan and link to Review without changing roadmap/check-in accounting.
3. **Individual Lesson Reports:** detail navigation with nested assessed passages; reuse final units/content and adaptation; retain limited older reports, per-passage transcript gate and original-audio GET. Keep “Assessed practice” available for unfinished lessons.
4. **Listening Patterns:** pure versioned aggregation under the approved rules; broad supported dimensions and explicit insufficient evidence. No chart or finer unsupported taxonomy.
5. **Testing and Accessibility:** run data/auth/report/browser coverage and all eight required CI jobs on a focused branch based on the reviewed integration revision. The current dirty checkout should not be wholesale committed or treated as the production baseline.
6. **Production Release:** separate owner approval after reviewed PR/green CI. No schema change planned. Roll back application changes to the verified schema-004-compatible release while retaining all learner records. If future scope requires schema changes, stop for a minimal additive migration proposal, rollback strategy, preserved-data verification and isolated restored-production-backup rehearsal before explicit production schema approval.

### Read API and performance proposal

Keep existing historical/transcript/audio endpoints. Add an owned Review summary/list projection and an owned structured-lesson/legacy-session detail projection rather than making the client issue one history/content request per row. Exact route names are an implementation choice after approval. Summary includes data availability and timezone provenance; list carries stable IDs, completion provenance, safe title/topic/levels, supported takeaway and report kind; detail returns only assessed-passage evidence, metadata and replay availability.

Use a bounded number of batch queries joining learner-owned lessons, links, exercises, final evaluations/events and completion timestamps. Keep incomplete lessons as left-joined limited entries where appropriate. Compute counts over the full eligible inventory independently of list pagination; fetch pattern units only for the bounded recent window. Exclude scripts, raw response text and audio bytes from overview payloads. Do not call `repo.completed()`, `result()` or `lesson_work()` in a per-row list loop. Measure query counts and large-history behavior before adding indexes/caches; avoid schema changes based on assumed performance.

`LearnerAuthentication` validates bearer identity with Supabase Auth and binds a scoped repository. All new reads must use that scope with explicit ownership predicates through every join, including lesson links and audio. The backend has privileged DB access, so do not rely on RLS alone; existing SQL enables RLS and denies browser table privileges. Return 404 for foreign resource IDs, 401 for missing/invalid auth, and retain transcript 403 before final assessment. Scope frontend state to account and abort stale requests on account/navigation changes. Review GETs must not invoke planner reconciliation, assessment, generation or speech providers.

## Verification plan and initial-audit result

Existing tests already cover transcript gates, nested cross-account resources/audio, restart persistence, incomplete evidence, bounded follow-ups, lesson completion and imports. They do not establish the new hub's counts, timezone semantics, pattern thresholds or UX.

Add fixtures for multiple passages in one lesson, resolved follow-ups, legacy/unlinked sessions, linked active/paused lessons, completed lessons with no assessment, missing content/metadata/timestamps/audio, incompatible score versions, partial/unknown distinctions, duplicate timestamps and pagination. Test midnight in positive/negative offsets, DST boundaries, invalid-zone UTC fallback, and unknown dates. Assert totals/day counts over all pages, stable ordering, final-only evidence, no invented insights and no provider calls. Test unauthorized/foreign summary/detail/transcript/audio access, including malformed links and stale account responses.

Browser checks: overview, report/back navigation, independent passage transcript expansion, missing-audio handling, loading/error/retry/empty/limited states, long titles, keyboard focus/accessible labels, screen-reader status, and 320px/mobile/desktop layouts without overflow. Preserve existing voice/onboarding/planner/check-in/1.8-second silence/accent/budget regression suites.

Required jobs identified in `origin/main:.github/workflows/ci.yml`: **Backend; PostgreSQL integration; Frontend; Browser; Security; Rollback schema 003; Rollback schema 004; CI required**. Frontend includes web and marketing lint/build/type checks; Browser includes mocked and native HTTP/audio acceptance. Use the workflow's offline provider isolation and disposable Postgres, not production credentials. Existing AUD-40 CI results are not AUD-41 verification.

Initial audit verification: read-only source/schema/API/test/workflow inspection and four Figma contexts/screenshots. No application tests or CI jobs run for this documentation-only phase; all eight remain required after implementation. No production data completeness or release claim is made.

## Approval decisions and deferred persistence

Owner approval requested for the initial implementation scope above, specifically:

- Structured lesson counts plus clearly labeled unlinked legacy lesson-equivalents; separately retain assessed practice from unfinished lessons.
- Current-display-timezone practice days with explicit UTC fallback and unknown-date disclosure; no claim of recovered historical local dates.
- Qualitative history in place of numerical trend/average, passage material level in place of certified learner proficiency, and conservative four-dimension patterns.
- Move completed-history presentation out of Plan while preserving its forward-looking planner and spoken check-ins.

Future finer skill tags require a versioned per-unit taxonomy persisted during ordinary assessment, with validation and no speculative historical backfill. Exact completion-local dates require a timezone snapshot at completion and a preference policy. Credible numeric progress requires an assessment comparability/calibration design and evaluator/rubric provenance, not merely another score column. None is necessary for this initial hub or authorized for implementation here. No approval for migration, deployment or paid provider calls is requested by this audit.
