# Supabase password recovery (AUD-27)

Implementation and controlled rollout documentation, 10 October 2026. Real email delivery and password-reset acceptance remain pending; AUD-27 stays In Progress.

## Flow and boundaries

The existing login links to `/forgot-password`. `resetPasswordForEmail` uses the existing public Supabase configuration. Known/unknown email addresses and account-specific HTTP errors produce identical neutral confirmation. Connectivity failures show a generic retry message; raw provider errors never reach the UI. Loading controls and a single-flight guard prevent duplicate submits; no application retries.

Production redirects are fixed to `https://app.audli.ai/auth/recovery`, regardless of browser host, previews, query parameters or user input. Only development builds on loopback use that exact local origin. No new provider or secret. See [password recovery](https://supabase.com/docs/guides/auth/passwords) and [redirect allowlists](https://supabase.com/docs/guides/auth/redirect-urls).

`/auth/recovery` supports default implicit `#type=recovery&access_token=…&refresh_token=…` callbacks and optional one-time `?type=recovery&token_hash=…` callbacks (`verifyOtp`). It strips query/fragment credentials and navigation arguments before calling Auth. Missing, duplicate, conflicting, non-recovery, error or expired credentials fail closed; a normal learner session never substitutes for a recovery link. Expired implicit tokens cannot silently refresh during initialization. The existing app uses implicit browser auth; PKCE code-only callbacks are rejected rather than changing signup/session architecture. Default links work without an original-browser verifier, including when requesting mail on one device and opening it on another.

Recovery uses a separate in-memory, non-persistent, non-refreshing Supabase client/channel. Supabase verifies credentials and `getUser` verifies/binds the user before editing. Expiry is checked again before submitting a password. React effect replay reuses one verification promise. The callback never mounts private lesson/onboarding state or requests learner data. Legacy recovery redirects to Site URL are forwarded to the fixed callback before the persistent auth client initializes. Normal signup/login/refresh remains in that existing client, which excludes recovery URL auto-detection.

Password and confirmation require 8–128 characters, matching values and non-whitespace content. Supabase's stronger password policy remains authoritative. Safe messages cover mismatch, weak/same password, expiry/invalid/reused links, and network failure. Success clears/hides inputs and offers sign-in. After URL scrubbing, a page refresh requires a new link; recovery credentials are not stored to make reload work.

A successful reset clears only matching learner credentials and notifies their tabs, while preserving a different account's browser session. It requests global sign-out of the recovered account's refresh sessions. Logout failure leaves password success intact and warns that session logout could not be confirmed. Existing access JWTs can remain valid until expiration; no immediate token-revocation guarantee is added. See [Supabase sessions](https://supabase.com/docs/guides/auth/sessions).

Supabase owns email OTP expiry and single use; rejected used links show Request a new link. A copied final implicit URL contains bearer credentials valid until expiry, not a separately single-use OTP. Treat it as sensitive. Token-hash verification offers direct authoritative one-time-link behavior, but puts the hash in an initial HTTP query; redact hosting access-log queries if enabling that template. Application code logs neither credentials nor passwords. Both dynamic pages send no-referrer, no-store and noindex/nofollow. Next's development server overrides Cache-Control with no-cache/must-revalidate; production headers require separate verification.

FastAPI is unchanged: all protected APIs retain verified bearer identity, ownership, origin/body guards and transcript gates. Query tokens, cookies and browser learner IDs never authenticate a request. No public backend recovery endpoint, privileged key, migration, RLS/table grant or TTS/session logic change.

## Supabase configuration before approved rollout

Verify Authentication → URL Configuration:

- Site URL: `https://app.audli.ai`.
- Add exact Redirect URL: `https://app.audli.ai/auth/recovery`.
- Preserve legitimate existing redirects; no production wildcard or HTTP URL.
- An approved development environment may separately allow its exact loopback callback, e.g. `http://127.0.0.1:3000/auth/recovery`. Mock tests on port 3100 need no real allowlist change.

The owner confirmed the production Site URL and exact recovery redirect above in the Supabase dashboard on 10 October 2026. The owner also confirmed the Reset Password template is the Supabase-managed default; its HTML is unavailable for editing without custom SMTP. No dashboard settings were changed by this implementation. Existing Vercel public Supabase variables are reused; no Render settings are needed.

The documented default Reset Password template using `{{ .ConfirmationURL }}` works without template changes: Supabase verifies the email link and redirects to the allowlisted requested URL with the session in the fragment, which this client-rendered callback handles. This conclusion combines the owner's default-template confirmation with current Supabase documentation; it is not an inspection of the production HTML or evidence of delivery. Custom SMTP is not required for callback compatibility or this controlled release. Existing custom templates must honor the requested redirect instead of an old hard-coded domain. Optionally, after separate approval, change only the Reset Password link to:

```html
<a href="{{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=recovery">Reset your Audli password</a>
```

The app supplies a fixed trusted RedirectTo. Leave signup/other templates unchanged. [Email templates](https://supabase.com/docs/guides/auth/auth-email-templates). New free-tier projects using default SMTP may restrict customization; custom SMTP or an eligible plan is required only for this optional template. [June 2026 change](https://supabase.com/changelog/46599-changes-to-email-template-customisation-on-free-tier).

Verify actual SMTP delivery/sender reputation, recovery expiry and existing rate limits during acceptance before declaring recovery complete. Preserve confirmation, password policy, CAPTCHA/MFA and session controls. If recovery CAPTCHA is enabled, integrate its approved challenge rather than disabling it; Audli currently has no login CAPTCHA widget. Supabase documents that its built-in sender restricts recipients to project organization team members, currently limits sending to two messages per hour and offers best-effort delivery. These are delivery limitations, not a template compatibility problem; ordinary learner delivery cannot be claimed without verification. Neutral confirmation is not proof of delivery. No test emails may be sent to unapproved addresses. [SMTP requirements](https://supabase.com/docs/guides/auth/auth-smtp).

## Verification and acceptance

Helper tests cover redirect pinning, callback parsing/expiry, password bounds and expiry-error classification. Browser tests use the installed Supabase SDK with intercepted Auth/API responses: both callback formats, neutral confirmation, loading/duplicates, one-time verification, password update/policy errors, invalid/expired/reused links, URL scrubbing, no learner requests, different-account preservation, matching-account cleanup, logout failure, responsive widths and security headers. Existing login/signup/refresh/account switch, recording, Retry/Resume, serial playback and transcript-gating tests run alongside recovery. Mock tests prove integration/control flow, not real signing/email delivery.

Backend tests prove recovery parameters/cookies cannot authenticate or initialize learners, select another account or bypass origin restrictions. Final counts are recorded in the AUD-27 local implementation report. Dependencies are unchanged.

After approved release/configuration review, dedicated confirmed test accounts should verify real known/unknown emails, mobile Safari mail-to-app navigation (including a different browser from the request), new-password sign-in, rejection of the old password, used/expired links, stronger configured policies, session logout, and another signed-in account's isolation. Check any CAPTCHA/MFA/reauthentication requirements and email-scanner behavior. No real-email/iPhone pass is claimed; AUD-27 remains In Progress pending owner acceptance.
