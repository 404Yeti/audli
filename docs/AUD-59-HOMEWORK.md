# Optional listening homework V0.1 — architecture review

Reviewed against main eeaf29e before implementation. Work is isolated in
`feat/AUD-59-listening-homework`; the original dirty worktree is untouched.

## Integration points

`lesson_api.save` commits the owned lesson before optional roadmap reconciliation.
`lesson.heard` transitions CLOSING to COMPLETED; the client then displays a separate
completion view. Offer homework there through a separate endpoint, after persistence
and closing audio, never inside assessment, adaptation, or terminal feedback.
`AdaptivePlan` renders the existing `/api/plan` three outlines and optional spoken
check-in. Add an independent homework section without changing those outlines.
Review remains a read-only view over assessed lesson history. Homework does not
enter it, comprehension scores, or planner context.

`LearnerAuthentication` verifies Supabase `/auth/v1/user`, scopes repositories with
ContextVar, and serializes mutations by account across workers. Reuse it and check
owner + completed lesson on every homework operation. Existing audio upload and
voice lifecycle helpers remain authoritative; no reflection recording in V0.1.
SQLAlchemy Core supports SQLite and Postgres; numbered SQL migrations are explicit,
backend-only tables use RLS plus revoked browser grants. Keep original migrations
byte-for-byte stable. New schema is additive and deployed only by owner action.

## Proposed model and provider boundary

Content: provider/id pair, canonical URL, title, summary, topic, language, duration,
publisher, nullable difficulty estimate/confidence, transcript authorization,
explicit playback/evaluation capabilities, validation time and availability.
Assignments: owner, completed lesson, content, objective, offered/assigned/completed/
discussed/skipped/unavailable state, timestamps, discovery lease and attempt count.
One record per owner/lesson and no repeated assigned content per owner. Reflections:
reserved owned table with grounding explicitly unavailable; no public write route.

Small typed provider protocol: search(plan), metadata(ids), validate(content, plan),
capabilities(). YouTube alone is registered. Application code builds a constrained
plan from sanitized topic/interests, approximate practice level and optional accent
hint; it validates and ranks metadata deterministically. No agent or LLM gets to
choose URLs or override eligibility. Playback is external only initially.

## YouTube requirements and bounds

Owner must enable YouTube Data API v3 and supply a server-only restricted API key;
this task does neither. Use fixed Google API endpoints, HTTPS, no redirects, one
search page of at most ten IDs, batched videos.list metadata, strict SafeSearch and
English relevance hint. Timeout the whole operation; no retries/pagination. Cache
search metadata for 15 minutes with bounded capacity; revalidate assigned links
before opening when older than an hour. Persist a fleet-wide daily admission
counter and per-assignment discovery limit to bound quota across workers/restarts.
Google's current search documentation describes a separate Search Queries quota
bucket (100 calls/day default, 1 unit per call); videos.list costs one unit. Confirm
actual project quotas in Cloud Console before enabling; do not assume historical
100-unit search accounting. Quota errors open a cooldown and return a normal
unavailable outcome. No credential or profile logging.

Sources: https://developers.google.com/youtube/data/reference/rest/v3/search/list
and https://developers.google.com/youtube/data/reference/rest/v3/videos/list.

## Eligibility and risks

Require real API-returned IDs, complete metadata, public processed non-live video,
bounded duration, declared English audio, topic overlap, no detectable age/content
warning, and compatible region restrictions. SafeSearch/metadata cannot prove
safety, accent, one-speaker delivery, or precise CEFR. Display that limitation and
leave estimated difficulty unknown. Never fetch arbitrary URLs, scrape captions,
or claim factual grounding: YouTube caption flags confer no authorization.

Discovery is a separate bounded request; failure cannot roll back completed lessons.
Unique constraints and a durable lease prevent duplicates across retries/processes.
Untrusted metadata is only parsed and rendered as escaped text, never instructions.
Feature flag defaults off. Disabled reads do not require homework tables. No
production migration/config/deployment is authorized.

## Deployment dependencies and future contract

Apply additive migration 005 to a disposable/staging database first, verify RLS and
constraints, back up production, and obtain owner approval before production.
The server accepts prior schemas with homework off; enabling requires new tables.
Set AUDLI_HOMEWORK_ENABLED, AUDLI_YOUTUBE_API_KEY and an explicit daily search
limit. No frontend credentials or new OAuth scopes. Roll back by disabling the flag;
retain records and migration. V0.2 can add owned reflection endpoints, not-yet,
one or two questions, and authorized-transcript grounding without connecting
reflections to existing scores/adaptation. Self-reported completion is not verified.

Credentials use the `X-Goog-Api-Key` header rather than URL parameters, per
[Google API key guidance](https://docs.cloud.google.com/docs/authentication/api-keys-best-practices).
HTTPX discovery URL logging is filtered so search interests do not enter request
logs. The daily admission ceiling also covers stale-link metadata rechecks, making
it conservative relative to actual search usage. Speech for the invitation is a
separate opt-in, cached conversational act with text and replay controls.

Shared content holds only public provider metadata (including the provider category).
Learner-selected search topics stay on owned assignments and never enter the
shared content snapshot, including when two accounts receive the same video.
