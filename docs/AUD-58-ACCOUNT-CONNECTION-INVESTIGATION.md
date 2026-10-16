# AUD-58: production account connection investigation

Investigated revision `a5ac73ad90e0ffbd39b14cd48438a215a3f80ade`, 2026-10-16 UTC.

## Finding and confidence

The exact screen is a **frontend initialization deadline**, not proof of invalid credentials, a failed profile, or a database outage. The incident's underlying pending operation is **not confirmed** without its browser request timings or incident-correlated service logs. Two distinct waits reproduce the exact message. No corrective auth-flow change or timeout increase is justified by the available evidence; this draft records findings, adds regression coverage, and now instruments the stages as described below.

`web/components/auth-gate.tsx:50–56` starts a single 20,000 ms deadline (`web/lib/startup.ts:3`). On expiration it deactivates the effect, unsubscribes from auth notifications, removes the token provider, and selects failed mode. Line 179 renders `AudliLoadingScreen`; `web/components/audli-loading-screen.tsx` supplies the branded heading and Retry button. The deadline begins before fetching config and clears on the first auth-state event, including an unauthenticated INITIAL_SESSION. It is not restarted after password login, Google return, or profile retrieval within that mount. Consequently this exact screen indicates initial startup/reload, not an ordinary later authenticated API failure.

## Initialization path and waits

1. AuthGate first forwards legacy recovery links to the isolated recovery route. Otherwise `api('/auth/config')` awaits same-origin `/api/auth/config` through the Next rewrite to Render, including headers and JSON body. This public endpoint returns only the auth mode, with no learner/profile database work or bearer verification (`app/auth.py`, `app/main.py`).
2. After config reports Supabase, `authClient()` constructs the singleton browser SDK with `audli-auth-session` storage. SDK 2.117.2 initializes from browser storage; a sufficiently fresh saved session needs no Auth network request. Expiring credentials cause a refresh-token request to Supabase before initial session notification. Email implicit callbacks can require Supabase user retrieval. Initialization, storage operations and any refresh/response body must settle before the INITIAL_SESSION notification. Audli supplies no custom SDK lock; do not attribute this to an SDK navigator-lock deadlock without evidence.
3. AuthGate subscribes with a synchronous callback. It does not await backend work inside the callback. The first event clears the deadline and sets session/readiness. Every protected API call subsequently calls `getSession()` through the token provider. This can itself refresh credentials, but it is then covered by the relevant operation's deadline, not this initial account deadline.
4. Google uses a separate sessionStorage PKCE client. `/auth/callback` validates a fresh same-tab transaction, strips the code from the URL, exchanges it, transfers credentials with learner `setSession`, and performs a full navigation to `/`. That callback has its **own** 20-second timer and different “Google sign-in is taking too long” message. The resulting root navigation starts AuthGate's deadline anew. No profile is fetched during the code exchange.
5. ApplicationFlow reads `/profile` after authenticated children mount. FastAPI verifies the bearer through Supabase `/auth/v1/user` (httpx 10-second request timeout, 5-second connect timeout), then selects the owner-scoped repository. Server verification maps invalid/unconfirmed/anonymous identities to 401 and unavailable Auth verification to 503. Browser UUIDs are never identity authority.
6. For onboarding, restore reads `/onboarding`, then `/profile` again for recognition settings; saved conversation intent and server checkpoints decide whether to offer resume or restart audio. For returning learners, Product repeats `/profile` in parallel with `/exercises/current`, then retrieves `/lessons/current`. These repeated profile reads add latency, and API calls each verify identity, but they occur **after** the account deadline has been cleared. They cannot explain this exact message. Their separate startup timers say “Your listening space is taking too long to connect.”

Config and SDK initialization are sequential, sharing one 20-second budget. For example, 12 seconds for config plus 9 seconds for refresh can exceed it although both services succeed individually. A stalled config fetch or Auth refresh can remain pending beyond Audli's timer: the frontend deactivates its continuation but does not abort the SDK initialization. Late success cannot mount private children. Retry calls `window.location.reload()`, discarding that document/SDK and genuinely re-fetching config and restoring/refreshing credentials; it preserves saved onboarding and does not automatically loop. React development Strict Mode can issue two config requests; that is not evidence of production duplication.

## Evidence and distinctions

Read-only production probes returned `/api/auth/config` HTTP 200 in 0.535092 seconds and Render `/api/health` HTTP 200 in 0.327804 seconds; health reported the supplied revision. These snapshots establish current reachability only. They do not exclude an earlier cold start, a client-specific route problem, Supabase refresh latency, or intermittent packet loss. No authenticated production learner requests or database mutations were made. Render MCP could not retrieve logs because no workspace was selected; Render CLI was unavailable. No outage or cold start is established.

A completed config HTTP 401/403/429/503 with JSON detail takes the error catch path immediately and displays that detail. Network rejection also takes the catch path. An HTML proxy 502 produces the API reachability message. A request that has not completed (including a hanging body or retrying Auth refresh) can instead lose to the deadline. A response status alone is therefore insufficient; completion timing matters. Auth initialization error mapping can also yield a null INITIAL_SESSION rather than this timeout. Backend profile 401 expires the session; profile 403/429/5xx surface downstream errors.

The timeout and error text were introduced in PR #37 (`be26499`, AUD-48). PR #39 (`81909da`) adds the Google client/callback without changing this timer or initial-session callback. PR #40 (`a5ac73a`) changes logout form clearing and onboarding account controls, not startup. This establishes code history, not causality or a regression attribution.

Supabase behavior was checked against the installed SDK source and [onAuthStateChange documentation](https://supabase.com/docs/reference/javascript/auth-onauthstatechange). The current changelog was reviewed; no relevant hosted-Auth breaking change was identified. Mock provider tests establish flow behavior, not live Google consent, hosted identity linking, or actual production token validity.

## Reproduction and verification

- Existing browser regression: sign in with SDK fixtures, expire the saved session timestamp, hold `/auth/v1/token`, reload, advance 20,001 ms. The exact account message appears without private content. Release the interception and click Retry: the same account restores.
- Added regression: retain valid saved credentials, hold config, reload, advance 20,001 ms. The exact message appears even though credentials remain saved and other mocked services are healthy. Make config available and click Retry: a new config request restores the same account.
- Added four cases distinguishing completed config 401/403/429/503 responses from the deadline.
- Existing Google PKCE return, callback failure/retry, email/account isolation, saved-onboarding account-menu resume, and downstream startup timeout tests are included in focused browser verification.

Focused Playwright authentication/startup suite: **23 passed**. Frontend unit suite, TypeScript, focused ESLint and diff whitespace checks passed; backend auth suite: **42 passed**.

## Smallest safe next action

Keep the existing deadline and full-document Retry while collecting **stage-only** evidence: config request completion time/status, whether INITIAL_SESSION arrived, and whether an Auth refresh was pending. Obtain an incident UTC time and a sanitized browser waterfall listing only host/path/status/timing; never retain authorization headers, query credentials, token bodies, HAR credentials, or learner payloads. Correlate with read-only Render request/instance events and Supabase Auth service health.

If serial latency is confirmed, consider overlapping SDK restoration with config discovery while withholding private rendering until server mode is verified. If config cold starts dominate, investigate that service path; if refresh stalls, address the observed Auth/network failure. Neither a larger timeout nor removing saved sessions is a justified incident fix now. The authorized follow-up below adds stage diagnostics to improve the next investigation. AUD-58 remains open pending incident-specific evidence. No deployment, configuration, schema, merge, or production changes are authorized by this report.

## Follow-up: privacy-safe startup timing

PR #41 now adds browser-local instrumentation; it does not claim to fix the unconfirmed incident. AuthGate buffers stage timings and flushes them as `[Audli auth startup]` warnings only on startup failure/deadline, an observed refresh failure, or recovery after the user explicitly clicks Retry following a timeout. Successful normal startup/refresh emits no diagnostic records. Nothing is uploaded to a telemetry service.

To investigate the next incident, open browser DevTools **Console**, enable **Preserve log** before clicking Retry, and filter for `[Audli auth startup]`. Share only those diagnostic records. Each record has exactly `stage`, rounded monotonic `elapsedMs`, and an allowlisted `category`; no URLs, payloads, errors, tokens, cookies, account identifiers or learner information are included.

| Records | Interpretation |
| --- | --- |
| `auth_config / pending` then `auth_gate_deadline / timeout` | Config has not completed its HTTP/JSON operation before the deadline. |
| `auth_config / http_4xx` or `http_5xx` | Config completed with an HTTP failure class. |
| `auth_config / network` | Config failed before response headers were observed. |
| `auth_config / invalid_response` | Successful HTTP status but unreadable/invalid response. |
| `auth_config / initialization` | Config resolved but auth mode/configuration could not initialize. |
| `auth_config / completed`, `session_restore / pending`, deadline | Config succeeded; SDK initial session notification is still pending. |
| Above plus `token_refresh / pending` | A refresh request is still awaiting response headers; its elapsed time isolates the network portion. |
| `token_refresh / completed`, session still pending | Refresh headers arrived; SDK body parsing/initialization has not yet finished. Refresh timing intentionally stops at headers without reading or cloning credential bodies. |
| `token_refresh / http_4xx`, `http_5xx`, or `network` | SDK refresh saw that sanitized failure class. SDK-owned retry/backoff can keep session restoration pending. Up to three recent refresh outcomes are retained. |
| `retry_connection / connected` | A fresh document reached AuthGate readiness after explicit Retry within five minutes of the timeout. This confirms auth initialization recovered, not backend profile/onboarding readiness or authenticated account validity. An unauthenticated ready state also counts as a connected Auth service. |

Config/session elapsed values are measured from each stage's start; the deadline/retry values are measured from that document's AuthGate initialization. The fixed deadline remains **20,000 ms** across the sequential config and session stages. Inactive effect cleanup stops diagnostics, and late results cannot emit additional records. A replacement development Strict Mode effect can observe an SDK refresh already in flight. The optional same-tab retry marker contains only the click timestamp, expires after five minutes, and is removed on completion/failure. If storage is blocked, startup/retry still works but cross-reload recovery cannot be identified. The Google callback has its existing separate deadline; these records diagnose AuthGate after it returns to `/`, not the PKCE exchange itself.

Implementation uses Supabase's supported [custom fetch option](https://supabase.com/docs/reference/javascript/initializing) on the persistent learner client only; it forwards requests, responses and failures unchanged. Google PKCE/recovery clients, bearer verification, account isolation, existing retry navigation and SDK retry policy are unchanged. TypeScript permits explicit `.ts` imports under the existing `noEmit` configuration so the existing Node native TypeScript unit suite can load the diagnostic dependency.

Follow-up verification: **24 focused Playwright tests passed**, including diagnostic payload checks, quiet normal refresh, Google return and saved-onboarding isolation. **8 diagnostic unit tests passed**, and the full frontend unit suite, ESLint/Oxlint, TypeScript, production build, lint coverage and diff whitespace checks passed. No live-provider or production incident reproduction is claimed.
