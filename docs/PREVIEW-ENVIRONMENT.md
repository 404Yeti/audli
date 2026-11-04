# Vercel Preview environment: exposure and proposed containment (AUD-97)

**Status: proposal only. No hosted environment variable was read or changed. Apply only with explicit approval.**

## Current exposure (observed 2026-11-04 via the Vercel API: variable names and targets only, never values)
| Variable | Targets | Effect on a Preview build |
| --- | --- | --- |
| `BACKEND_URL` (sensitive) | Production **and** Preview | `next.config.ts` rewrites `/api/*` to it, so every Preview frontend proxies to the same API as Production |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Preview and Production | A Preview can sign users in against the production Supabase Auth project (public, publishable values by design) |

What is **not** exposed: no database URL, service-role key, storage credentials or provider keys are configured in Vercel. Previews are behind Vercel
SSO protection (`all_except_custom_domains`), so only team members can open them. Residual risk: a team member opening any PR's preview works against
**production data and accounts** (Auth sessions plus the production API), with unreviewed frontend code from that PR. The API's origin check refuses
cross-origin browser writes from a preview origin, which limits but does not remove this.

## Minimum safe Preview-only configuration
1. Give Preview its own `BACKEND_URL` that cannot reach production:
   - **Preferred when it exists:** a separate staging API with its own database and Supabase project.
   - **Until then (intentionally nonfunctional):** `https://preview-disabled.invalid`. The `.invalid` TLD is reserved and never resolves, so every `/api/*`
     call from a preview fails fast with the app's existing "could not reach the server" state and nothing reaches production.
2. Because `BACKEND_URL` is a single variable with both targets, **edit it in the dashboard** (untick Preview), then add a new Preview-only variable.
   Do not delete-and-recreate the Production value: it is sensitive and cannot be read back.
3. Leave Production values and all Production-only variables untouched.
4. Optional hardening: scope the two `NEXT_PUBLIC_SUPABASE_*` variables to Production only. A Preview then shows the sign-in-not-configured state instead of
   offering production sign-in. Do this only if previews are not needed to exercise authentication.
5. Keep SSO protection on.

## Verifying after the change
Open a Preview of any PR: `/api/health` must fail with the unreachable-server message, and the production API's logs must show no request from that preview.

## Trade-off
Preview deployments stop being able to demo real data. Mocked browser tests in CI do not use Vercel previews, so CI is unaffected.
