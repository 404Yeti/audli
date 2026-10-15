# AUD-55 Google sign-in

Implementation is reviewable; live Google sign-in and identity linking are **not certified**. Provider credentials and real consent/account tests are manual release blockers. Do not merge or deploy without owner approval. No database migration or email-based learner merge is introduced.

## Manual setup

1. In Google Cloud Console → Google Auth Platform, select/create the Audli project. Configure Branding (Audli, support email, app homepage and published privacy/terms URLs under audli.ai), Audience (External; add designated test users while Testing), and developer contact. Use only `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/userinfo.profile` in Data Access. No Gmail, Drive, YouTube, offline access or extra APIs.
2. Clients → Create OAuth client → Web application. Authorized JavaScript origins: `https://app.audli.ai`; for a separate development client add `http://127.0.0.1:3000` and `http://localhost:3000`. Authorized redirect URI: **the exact Supabase Google provider callback**, `https://<PROJECT_REF>.supabase.co/auth/v1/callback`. This is Google's callback, not Audli's `/auth/callback`. Use the development Supabase project's callback for the development client. Save the client ID/secret privately.
3. Supabase Dashboard → Authentication → Sign In / Providers → Google: enable, enter that client ID and secret. Keep nonce validation enabled; do not enable skip nonce check or allow users without an email. Keep email/password and email confirmation enabled. Manual identity linking is unnecessary; do not enable it for this feature.
4. Authentication → URL Configuration: production Site URL `https://app.audli.ai`. Add the exact redirect `https://app.audli.ai/auth/callback`. Preserve existing confirmation and password recovery redirects. On the development project add `http://127.0.0.1:3000/auth/callback` and `http://localhost:3000/auth/callback`. Browser fixtures use `http://127.0.0.1:3100/auth/callback`; allow it only on the development project if manually testing there. Never allow wildcard production/preview redirects. Browser origin validation matches these explicit origins; unsupported hosts fail closed.
5. Production web build environment: `NEXT_PUBLIC_SUPABASE_URL=https://<PROJECT_REF>.supabase.co`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_<PUBLIC_KEY>`. API: retain `AUDLI_AUTH_MODE=supabase`, `AUDLI_SUPABASE_URL` and `AUDLI_SUPABASE_PUBLISHABLE_KEY` for the same project, existing production persistence, CORS and access controls. No Google secret belongs in Audli environment variables or source control.
6. Authenticated local development: set the same web variable names in ignored `web/.env.local` and backend auth variables in the ignored backend environment, preferably using a separate development Supabase project. Run the existing FastAPI server on 8000 and `cd web && npm run dev` on 127.0.0.1:3000. Local mode deliberately bypasses account UI; use Supabase mode to exercise OAuth. This repository uses hosted HTTPS Supabase, not a local Supabase CLI stack. Do not add HTTP Supabase exceptions to production URL validation.
7. Before public release, publish the Google OAuth audience if needed and complete any branding/domain verification Google requires. Confirm actual consent scopes and redirect URLs in a real browser. Never paste secrets or callback tokens into PR/Linear evidence.

## Security and ownership

Google uses a same-tab redirect (no popup dependency), SDK S256 PKCE, Supabase-managed provider state, and explicit code exchange. A ten-minute same-tab pending marker rejects unsolicited/expired callbacks; it is not a substitute for Supabase's state and PKCE validation. Temporary PKCE/session data uses `sessionStorage` under a separate SDK key, with no automatic URL detection or refresh. Callback removes query/fragment data before network activity, rejects implicit token fragments, provides generic failures, and transfers only Supabase access/refresh credentials into the existing learner client. No provider tokens are copied to learner storage. Temporary storage is cleared without signing out, since revocation would revoke the transferred session too. Callback has no-store/no-referrer/noindex headers. Unknown `next` parameters never control navigation.

Existing email confirmation and isolated password recovery retain their implicit flows. API still verifies access tokens against Supabase `/auth/v1/user`, requires confirmed email, rejects anonymous users, and scopes repositories by returned UUID. Google metadata/email is never used for authorization. New UUIDs use existing onboarding routing; existing UUIDs retain profile, lessons, Review, planner, onboarding and voice preferences. Different UUIDs remain isolated even if emails match. No user profile migration is performed.

## Identity linking audit and required live tests

[Supabase's identity linking documentation](https://supabase.com/docs/guides/auth/auth-identity-linking) says OAuth can automatically link to an existing email identity and removes other unconfirmed identities to prevent pre-account takeover. This is provider behavior, not permission for Audli to merge accounts. SAML SSO identities are excluded. OAuth-first email signup can return an obfuscated response without sending confirmation; it does not create a password. Adding a password is a separate authenticated account-management operation.

The mocked browser tests verify the actual installed JS SDK's PKCE request, transfer, routing and account boundary. They **do not establish hosted Auth linking behavior**. Before enabling production, use disposable accounts in the configured development project and record only IDs/identity provider names and pass/fail outcomes:

| Scenario | Required observation | Status |
| --- | --- | --- |
| New Google email | One new Auth UUID; onboarding; no other learner data | Pending real consent |
| Existing confirmed email/password → same Google email | Inspect Auth UUID and identities before/after; same UUID; saved history/planner/Review/onboarding/voice preferences retained; original password still works | Pending real linking |
| Existing unconfirmed email → Google | Confirm server behavior removes unsafe unconfirmed identities; pre-registered password cannot take over; inspect sessions and UUID rather than assuming safe email equality | Pending real linking |
| Google-first → email signup | Observe non-enumerating response; no duplicate learner or implicit password creation | Pending real provider test |
| Two different Google users | Distinct UUIDs; no cross-account reads/mutations; switch/reload/logout discard old learner state | Pending real consent |
| Deny consent / Back / interrupted network / expired or replayed callback / different browser | Safe retry or email fallback; no learner creation from failed flow | Mock callback rejection covered; real browsers pending |
| iOS Safari, Android Chrome, desktop Chrome/Safari | Same-tab consent, callback, persistence and logout; no popup requirement | Chromium fixtures covered; real devices pending |

Keep AUD-55 open until these provider-dependent checks pass. If UUIDs do not match in a linking case, stop and investigate; never merge learner records by matching email.

References: [Google provider setup](https://supabase.com/docs/guides/auth/social-login/auth-google), [PKCE](https://supabase.com/docs/guides/auth/sessions/pkce-flow), [redirect allowlist](https://supabase.com/docs/guides/auth/redirect-urls). Changelog reviewed 2026-10-15: upcoming TLS CBC cipher removal affects obsolete clients, not this OAuth API; modern mobile/desktop browsers remain the target.

## Verification evidence

Local: 42 backend auth tests passed; frontend unit suite, lint and TypeScript passed; all 8 Google browser tests passed. Tests include new-user onboarding, returning-user resume, real SDK PKCE plumbing, email regression after Google, account switching, consent denial, invalid/unsolicited callbacks, network failure, stalled-exchange retry and responsive layouts. Hosted provider linking is explicitly pending above.

Screenshots: [mobile sign-in](aud55/google-signin-390.png), [mobile registration](aud55/google-registration-390.png), [desktop sign-in](aud55/google-signin-1440.png), [desktop registration](aud55/google-registration-1440.png). Synthetic browser fixture; no real learner data.

PR branch disables Vercel previews in both projects; commits include `[skip render]`. No merge or deployment is authorized. All eight mandatory CI jobs must pass on the PR: Backend, PostgreSQL integration, Rollback schema 003, Rollback schema 004, Frontend, Browser, Security, CI required. See PR checks for authoritative results.
