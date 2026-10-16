# Homework V0.1 release readiness

Recommendation: **NO-GO for enabling the requested voice-first learner journey.**
The V0.1 button-based foundation can be reviewed independently, but owner approval
and live YouTube acceptance remain necessary. No V0.2 reflection work is included.

## Learner journey: supported behavior versus gaps

| Step | Evidence / result |
| --- | --- |
| Normal lesson completes and saves | Offline integration test drives real lesson/conversation routes with prescribed speech/evaluation fixtures through CLOSING → COMPLETED. Homework is a separate request after that commit. |
| Natural spoken homework offer | **Gap:** completion shows a visual invitation. “Hear invitation” explicitly plays fixed cached speech; there is no automatic conversational offer. |
| Spoken acceptance / decline | **Gap:** buttons only. No homework decision STT or spoken-intent endpoint exists. Do not describe button clicks as voice acceptance. |
| Constrained discovery | Application uses sanitized interests/topic, current practice level, recent lesson topic and optional accent hint. One query, ten IDs, batched metadata, deterministic filtering/ranking. |
| Real YouTube candidates | **Unverified live:** official adapter parsing/validation tested with mocked HTTP only. No real YouTube calls made for this review. |
| Assignment persistence | Real owned database transactions tested; owner/lesson uniqueness and durable lease prevent retry duplicates. |
| Spoken confirmation | **Gap:** assignment title/objective/link are shown visually. No assignment-confirmation speech is implemented. |
| Plan and external link | Actual UI tested with synthetic responses; same three planned lessons remain. Exact YouTube canonical URL allowlist; new-tab link names and noreferrer/noopener. Opening a real video remains manual acceptance. |
| Review history | Real Review API/report retains the completed lesson after assignment. UI completion screenshots restore a synthetic saved checkpoint and do not claim a real spoken lesson. |

Speech checks verify explicit invitation audio is requested only by a gesture,
replay is cached, and decline/refresh do not trigger spoken prompts. Acoustic
naturalness with the production tutor voice has **not** been assessed. Existing
native HTTP/audio CI acceptance covers core lesson media, not a complete spoken
homework dialogue.

## Confirmed V0.1 fixes in this review

- A refreshed page previously had no signal that discovery was already running.
  Responses now expose only a boolean lease status; a new owned GET endpoint and
  finite read-only polling recover its saved result without starting another search.
  An expired lease stops pending presentation; no automatic rediscovery occurs.
- Lost response after commit previously left the invitation looking unanswered.
  “Reload saved assignment” recovers the authoritative state without another acceptance.
- Homework fetches previously lacked a browser deadline. Requests/body reads now
  have a 25-second deadline, bounded above the server's ten-second discovery budget.
- Cached links could age while Plan stayed mounted. Clicking an aged link now
  revalidates before external navigation. Invalid validation dates cannot create a
  fresh link; removed content becomes unavailable.
- Status transitions removed the focused action button. Focus now moves to the
  assignment article after a transition, preserving completion-heading focus on mount.

## Failure acceptance matrix

| Condition | Behavior / evidence |
| --- | --- |
| Decline | Persisted skipped; no discovery, score change, or automatic speech on refresh. |
| No eligible results | Persist unavailable with a normal “lesson is saved” message. |
| API timeout | HTTP timeouts plus ten-second operation timeout; unavailable, saved lesson intact. Tests inject timeout; live network timing unverified. |
| Quota exhaustion | HTTP 403/429 cooldown; ordinary unavailable result. Fleet admission ceiling survives restarts. |
| Removed/private/blocked video | Fresh metadata excludes it, saved assignment becomes unavailable; no lesson rollback. |
| Repeated acceptance | Existing assignment returned; unique owner/lesson and owner/content constraints; durable lease prevents simultaneous discovery. |
| Refresh during discovery | Read-only polling resumes the saved lease/result. No extra acceptance or prompt audio. |
| Lost network response | Bounded error and explicit reload of owned saved assignment. No forced lesson repetition. |
| Hung frontend response | Browser aborts after 25 seconds and restores controls; covered by a real-duration browser fault injection. |
| Flag disabled | Empty disabled API response without optional table access; Plan keeps three lessons and ordinary completion/Review work. |

## Provider independence and limits

Assignment columns are provider-neutral; YouTube IDs and URL rules live in its
adapter. Capabilities distinguish external/embedded playback, authorized transcript
access and grounded evaluation. No agent/LLM selects URLs or sees metadata as
instructions. Transcript flags are not authorization; factual reflection scoring is
absent, and homework never feeds comprehension scores or planner adaptation.

One provider is registered in V0.1. Adding another requires registering/selecting an
adapter and extending the frontend provider URL allowlist; the current frontend
allowlist intentionally supports only YouTube. Plan composition and lesson completion
need no redesign, but this is **not** a finished multi-provider switching implementation.
No additional provider was implemented in this review.

## Migration, isolation and recovery checklist

- [ ] Owner verifies migration 005 on disposable/staging PostgreSQL and takes a consistent backup before any production work.
- [ ] Run the explicit `python -m scripts.migrate_postgres` as backend DB owner only after approval. No application startup migrations in production.
- [ ] Inspect composite lesson/owner and reflection/owner foreign keys, owner/lesson and owner/content uniqueness, RLS, and revoked PUBLIC/anon/authenticated grants.
- [ ] Verify Supabase bearer-authenticated account A cannot read or mutate account B's assignments, including the new saved-assignment GET route.
- [ ] Retain migration 005 and disable `AUDLI_HOMEWORK_ENABLED` for safe functional recovery. This is additive; there is no automatic destructive down migration.
- [ ] Use this release with the flag off for recovery: older binaries may reject schema 005. Restore a verified backup only under a separate, explicit rollback plan; do not delete lesson/history data to downgrade.

All tables are backend-only with RLS enabled and browser grants revoked; there are
no browser-owner policies because direct browser access is intentionally denied.
Request scope comes from server-verified Supabase identity. Shared metadata contains
only public source fields; private topics stay on owned assignments. Secrets use a
server-only SecretStr and X-Goog-Api-Key header; discovery request URL logging is
filtered. External metadata is escaped text, never commands. HTTPS fixed API hosts,
no redirects, canonical URL allowlists, strict schemas and bounded results prevent
arbitrary-link assignment/SSRF. No production credentials/data were read or exposed.

## YouTube configuration / quota checklist

Owner action only; none of these services or credentials were enabled or changed.

- [ ] Select an owner-controlled Google Cloud project and enable YouTube Data API v3; create a server-only API key restricted to that API (and approved server egress if applicable).
- [ ] Set `AUDLI_YOUTUBE_API_KEY` on the backend only, never NEXT_PUBLIC.
- [ ] Keep `AUDLI_HOMEWORK_ENABLED=false` until acceptance/approval.
- [ ] Choose `AUDLI_HOMEWORK_DAILY_SEARCH_LIMIT` explicitly (default 0, maximum 100); this is a conservative fleet admission count, not an API billing meter.
- [ ] Optionally set `AUDLI_HOMEWORK_REGION` to an ISO country code. Without it, detectable region restrictions cause exclusion.
- [ ] Check actual project quotas and other clients' use in Cloud Console before setting the ceiling.

A cold discovery uses one search.list request and, if IDs exist, one videos.list
request. Cache hits reuse up to ten candidates for 15 minutes (128 cached plans per
process); the fleet admission counter still counts the request conservatively.
Duplicate content is reused globally, but it cannot be assigned again to the same
learner. Repeated acceptance of an assigned record does not search again. Stale-link
rechecks use one metadata request and consume one conservative admission.

Current official docs describe 100 search.list calls/day in a dedicated Search
Queries bucket at one unit/call, plus 10,000 daily units for other endpoints;
videos.list costs one unit. Quotas reset on the provider's schedule, while Audli's
admission counter resets at UTC midnight, so its ceiling is not a guarantee against
project quota exhaustion. Confirm the project's actual quotas; older articles citing
100 units/search describe earlier accounting.

Missing credentials with a positive configured admission ceiling produce unavailable
without an external request. Default zero ceiling rejects discovery before provider
access. The API does not enable billing, buy quota, retry endlessly or raise quotas.
Quota increases are an owner/provider process and can require compliance review;
other services in the Cloud project may have separate billing. No paid service was
enabled or live provider call performed here. Explicit invitation playback can use
Audli’s existing paid TTS provider; its per-owner audio cache avoids repeated
provider generation for replay. This does not provision or reconfigure TTS.

Sources checked: [quota calculator](https://developers.google.com/youtube/data/determine_quota_cost),
[search.list](https://developers.google.com/youtube/data/reference/rest/v3/search/list),
[videos.list](https://developers.google.com/youtube/data/reference/rest/v3/videos/list),
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).

## UI evidence

All images use clearly labeled synthetic fixtures; they are not proof of live video
availability. Completion offer screenshots restore a saved checkpoint. Browser checks
cover keyboard Enter, focus after decisions, labeled topic input, live status/error
messages, safe link semantics, and absence of horizontal overflow. No assistive-device
or human screen-reader session was performed; semantic/keyboard checks are not a
claim of a full screen-reader audit.

| Width | Offer | Saved Plan | Unavailable | Disabled Plan |
| --- | --- | --- | --- | --- |
| 320 | [image](screenshots/AUD-59-ready-offer-320.png) | [image](screenshots/AUD-59-ready-plan-320.png) | [image](screenshots/AUD-59-ready-unavailable-320.png) | [image](screenshots/AUD-59-ready-disabled-320.png) |
| 390 | [image](screenshots/AUD-59-ready-offer-390.png) | [image](screenshots/AUD-59-ready-plan-390.png) | [image](screenshots/AUD-59-ready-unavailable-390.png) | [image](screenshots/AUD-59-ready-disabled-390.png) |
| 1440 | [image](screenshots/AUD-59-ready-offer-1440.png) | [image](screenshots/AUD-59-ready-plan-1440.png) | [image](screenshots/AUD-59-ready-unavailable-1440.png) | [image](screenshots/AUD-59-ready-disabled-1440.png) |

## Owner acceptance / release decision

1. Resolve or explicitly accept the V0.1 voice gaps before enabling the advertised journey. Voice-based decisions and spoken confirmation are not implemented by this readiness review.
2. With separate approval, configure a nonproduction API key/explicit budget and apply migration 005 to staging. Complete an ordinary spoken lesson and confirm Review/history first.
3. Use the actual implemented buttons, with explicit invitation playback if desired. Accept an existing interest and a different topic; inspect the real API-returned candidate and open the real YouTube link in the target region.
4. Confirm real content suitability by listening: metadata does not establish CEFR, accent, safety or one clear speaker. Do not grade a summary without authorized verified transcript content.
5. Decline, refresh during discovery, repeat acceptance, simulate quota/timeout/lost response and remove/block a test video. Confirm one assignment, no repeated speech, no loss of completed lesson or scores, and Plan/Review intact.
6. Test two authenticated accounts and a human keyboard/screen-reader session on 320px/390px/desktop. Disable the flag and confirm normal lessons remain available.
7. Obtain owner approval for a separately scoped release. This PR remains draft; no merge, deployment, enabling, migration or credential change is performed here.

**Live YouTube API calls are required for final owner acceptance.** Mock tests prove
plumbing and deterministic gates, not API project configuration, actual candidate
availability, real content suitability, regional playback or a natural spoken journey.

## Release checks

Final execution results will be appended after local focused tests and all eight
required CI checks complete. Baseline CI success is not substituted for new-head CI.

### Local review execution

- Focused homework contracts including real lesson-to-homework-to-Review HTTP/domain flow with the official adapter mocked: **47 passed**.
- Combined homework, authentication, lesson and planner regression run after the new owned GET checks: **171 passed**.
- Corrected homework/browser run: **10 passed**; final release-readiness rerun after focus preservation: **7 passed**. The 25-second stalled-request test exercised the real browser abort deadline.
- Web unit tests, ESLint/Oxlint, lint-rule coverage, production build and TypeScript passed. Final lint/TypeScript also passed after the focus adjustment.
- First screenshot/recovery browser attempts had four harness failures: a fixture restored completion on every reload and an alert selector matched Next's route announcer. The fixtures/selectors were corrected and subsequent runs passed; these were not recorded as successful runs.
- Screenshots were visually inspected at 320px and 390px, with all 12 requested captures produced. Human screen-reader/audio naturalness checks and real YouTube calls remain unverified.

### Publication boundary

Baseline `540455e` passed all eight hosted CI checks. The focused local fixes in
`7d32fae` have the results listed above; their hosted results must be checked on
the published head separately.

The owner authorized safe source publication after verification of the preview
patch. Both Vercel configurations now add only
`"feat/AUD-59-listening-homework": false` to `git.deploymentEnabled`.
[Vercel documents](https://vercel.com/docs/project-configuration/git-configuration#git.deploymentenabled)
that unspecified branches default to enabled. Neither configuration contains an
overlapping wildcard rule. Production/main and other branch behavior are unchanged.
No workflow or protection settings were modified. Main's eight required contexts
are Backend, PostgreSQL integration, Rollback schema 003, Rollback schema 004,
Frontend, Browser, Security and CI required; Vercel is not required.

The review bundle on Linear contains the report, 12 screenshots and source patches.
Publication is not release approval: homework stays disabled and **NO-GO** until
voice-first acceptance and real YouTube discovery are verified. The PR records
new-head CI and deployment observations after publication.
