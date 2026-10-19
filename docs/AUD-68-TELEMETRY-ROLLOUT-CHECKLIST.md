# Telemetry rollout checklist (AUD-68 / PR #51)

**Merging to `main` deploys to production**: Render `audli-api` has auto-deploy on every commit to `main` (Docker, single free-plan instance,
0.15 vCPU / 512 MB, no health-check path, ~40–60 s) and Vercel builds the web app from `main`. There is no separate "deploy" step to hold back.
Do each step only with the owner's explicit approval. Nothing below enables per-learner locking (`AUDLI_PER_LEARNER_LOCKS` stays unset).

Rollback target (before this release): Render deploy `dep-dbablp67bikc73ef8si0`, commit `289e093`. `/api/health` reports `revision`.

## Exact flags
| Flag | Where | Value | Effect |
|---|---|---|---|
| `AUDLI_LATENCY_AGGREGATES` | Render `audli-api` env | `true` (step 3) | Server stage timings + route templates + lock gauges logged as `Latency summary` lines; telemetry route records |
| `AUDLI_LATENCY_SUMMARY_SECONDS` | Render env | leave unset (300) | Window length |
| `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING` | Vercel project `audli`, **Production**, build-time | `1` (step 5) | Browser sends one report per meaningful turn. Needs a **new build**; removing it needs another. |
| `AUDLI_PER_LEARNER_LOCKS` | — | **leave unset** | Not part of this rollout |

Vercel state (read-only): the Vercel connector is not authorized for scope `robert-leybas-projects` (403), the CLI is not installed/logged in here, previews sit
behind Vercel Authentication, and the existing production bundle shows `NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY` was **not** set at build (it was not inlined). The new
flag is therefore assumed **not configured**; confirm in the dashboard (Project → Settings → Environment Variables) or via `npx vercel@latest env ls --scope robert-leybas-projects`
after `vercel login`. Both flags are safe in either order (backend flag off ⇒ the route returns 204 and records nothing).

## Steps
1. **Pre-merge:** #51 ready for review, all 8 required checks green on the final head, owner approves merging + the timing (low-traffic; expect ≈1 min interruption and a cold start).
2. **Merge #51 (squash) ⇒ auto-deploys.** Verify: `curl -s https://audli-api.onrender.com/api/health` → `status: ok` and `revision` = the squash commit
   (first request after a deploy/idle may take ~20 s). Render deploy `live`. No `Latency summary` lines yet (flag off). Sign in on your own account, run one lesson turn.
3. **Backend flag:** set `AUDLI_LATENCY_AGGREGATES=true` (Render restarts the service). Run a few turns. Expect `Latency summary {…}` lines within 5 min or at idle shutdown.
4. **Backend smoke tests**
   - New code is live: unauthenticated `curl -i -X POST https://app.audli.ai/api/telemetry/turn -H 'content-type: application/json' -d '{"stages":{"assessment":5}}'` → **401** (404 would mean old code).
   - Log shape: lines start `Latency summary {"window":N,"part":i,"parts":m,…}`, each well under 8 KB; `stages` include `http.POST /api/…` templates, `turn.assessment`, `turn.*.lock_wait/lock_hold`.
   - Privacy: Render log searches over `Latency summary` for UUID `[0-9a-f]{8}-[0-9a-f]{4}-`, `Bearer`, `@`, `eyJ` must return **no matches**.
   - No new 5xx/`Operation failed` after the flag change; `Tutor TTS` events unchanged.
5. **Frontend flag:** set `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1` for Production in Vercel, redeploy `main` (new build). Run a few turns on your account.
6. **Frontend smoke tests:** in Render logs a `client.turn_to_playback` (and other `client.*`) stage appears in the summary; browser console shows no errors; the lesson works identically; the report never blocks (a failed report is ignored).
7. **Observe 24–48 h.** Do not change anything else.

## Sample-count requirements (so p50/p90 mean something)
Quantiles come from histogram buckets (25 ms … 60 s) and are **upper bounds**, e.g. an assessment p50 ≈ 2.4 s reports as "≤ 3000 ms".
95% sampling uncertainty of the *rank* (percentile points) for n samples of one stage in one slice: 

| n | p50 ± | p90 ± |
|---|---|---|
| 30 | 17.9 | 10.7 |
| 50 | 13.9 | 8.3 |
| 100 | 9.8 | 5.9 |
| 150 | 8.0 | 4.8 |
| 200 | 6.9 | 4.2 |
| 400 | 4.9 | 2.9 |

Reporting rules: **n < 30** → show counts only. **30 ≤ n < 100** → label *indicative*. **p50 needs n ≥ 100** (true rank within ±10 points); **p90 needs n ≥ 140–150** (±5 points); use n ≥ 400 to compare two
configurations (e.g. before/after locking). Count per *stage and slice* (route, flag state, warm vs cold); never pool across a deploy, a flag change or a cold start. Drop or tag windows that contain a
`configuration` log event within the preceding 60 s (service start; the free plan sleeps when idle and a cold request took ~22 s on 2026-10-19).
Rate estimate [E]: retained production logs show ~32 assessed turns in ~31 h (includes owner testing), so n≈150 for `turn.assessment` needs roughly 5–7 days at today's usage. If that is too slow, run owner-driven sessions on a test account; do not use real learners' data.

## Rollback (fast → slow)
1. Disable client reports: remove `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING` in Vercel and redeploy (or Vercel dashboard rollback to the previous production deployment).
2. Disable server aggregation: set `AUDLI_LATENCY_AGGREGATES=false` (or unset) on Render; service restarts; route reverts to a 204 no-op, nothing is logged.
3. Code rollback: Render dashboard → `audli-api` → redeploy `dep-dbablp67bikc73ef8si0` (`289e093`), or `git revert` the squash commit on `main` (that itself auto-deploys). No schema, migration or data is involved in #51.
4. Confirm via `/api/health` `revision` and the absence of `Latency summary` lines.
Not verified: whether a Render dashboard rollback pauses auto-deploy; check the dashboard state after any manual rollback.

## Abort criteria (stop and roll back flags)
Any learner-visible change; new `Operation failed`/5xx attributable to telemetry; a summary line containing an ID/credential pattern; sustained CPU/memory above the instance limits
(512 MB; baseline ≈80–130 MB); or telemetry requests appearing as 409/401 storms.
