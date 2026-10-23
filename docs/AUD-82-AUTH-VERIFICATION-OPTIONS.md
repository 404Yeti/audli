# AUD-82 — Reducing repeated Supabase Auth calls: options and trade-offs (design only)

Status: **design for review. Nothing is implemented or deployed; production authentication is unchanged.** Decide with measurements: the `auth.verify` stage and `req.*.auth_verify` totals from PR #81 give the real cost once authenticated lessons run.

## Today
`SupabaseIdentity.verify` (`app/auth.py`) runs before every protected request: it validates the header shape (exactly one `Authorization`, Bearer, at most 8,192 characters, no whitespace), calls `GET <project>/auth/v1/user` with the publishable key, and accepts only HTTP 200 whose user is **not anonymous and has `email_confirmed_at`**; 400/401/403 become 401, anything else 503. The returned `id` is the learner identity for the whole request. Nothing is cached. Production runs one Render instance in Singapore; the Supabase project is in Tokyo (ap-northeast-1).

Estimated cost, **to be replaced by measurement**: roughly 100-200 ms per call (Singapore to Tokyo round trip plus Auth processing), several calls per lesson turn.

## Option A (candidate): bounded successful-verification cache, 30-60 s
Cache the result of a **successful** verification in process memory so repeated requests with the same token skip the network call.

**Design**
* Key: `sha256(token)`; the raw token is never stored, logged or returned. Value: `(learner_id, expires_at)` using a monotonic clock.
* TTL: `AUDLI_AUTH_CACHE_SECONDS`, **default 0 (off)**, maximum 60. Effective expiry is `min(TTL, token_exp - now)`, where `exp` is read from the token payload only to cap the lifetime (the cache is populated only after Supabase accepted the token, so the unsigned read cannot grant anything). If `exp` cannot be read, the entry is not cached.
* Only HTTP 200 results that passed the existing anonymous / confirmed-email checks are cached. 400/401/403, 5xx, timeouts and malformed bodies are **never** cached (no negative caching, so an outage or a refreshed token cannot be locked out).
* Bound: at most 1,024 entries, least-recently-used eviction (an entry is about 150 bytes, well under 1 MB), expired entries dropped on lookup and on insert. Per process; the single Render instance is the only user, and a second instance would simply verify on its own.
* Single flight: concurrent requests with the same token during a miss share one upstream call; its failure is not stored.
* Every other check stays per request: header validation, `LocalRequestGuard` origin and size rules, per-request lease, ownership scoping, the database account read.

**Isolation.** The key is the hash of the complete token, so one learner's token cannot select another's entry; the stored identity is exactly what Supabase returned for that token. A token carries one `sub`, so there is no cross-account lookup path.

**Revocation, logout and expiry trade-offs (the real cost)**
| Event | Today | With a 60 s cache |
|---|---|---|
| Normal expiry | Rejected by Auth at once | Capped by `exp`: never accepted past expiry |
| Token refresh by the app | New token is verified | New token is a new key: one miss, then cached |
| Sign-out, "sign out everywhere", ban, password reset | Expected to fail at the next request (**confirm with a revoked-token test before relying on it**) | Old token still accepted for **at most 60 s** |
| Auth user deleted (account deletion enabled) | Auth refuses | Still accepted for up to the TTL, **but** the database read finds no account and `initialize_learner` re-checks `auth.users` (`AccountUnavailable` gives 401) |
| Supabase outage | Every request 503 | Cached tokens keep working for up to the TTL; this is a small availability gain, not a security gain |
Mitigations if needed: a shorter TTL (30 s), clearing the entry from an explicit sign-out call, and keeping the cache disabled for write endpoints with irreversible effects (none identified; the lesson writes are scoped and idempotent).

**Observability (numbers only).** Hit and miss counts as stage counters (`auth.verify_cache_hit`, `auth.verify_cache_miss`), the existing `auth.verify` span on misses only, plus the cache size as a bounded gauge. No token, hash or identifier is ever emitted.

**Tests required before any implementation is merged**
1. Disabled by default and identical to today when `AUDLI_AUTH_CACHE_SECONDS=0` (the existing auth suite unchanged).
2. Hit, miss and expiry with an injected clock; `min(TTL, exp)` cap; unreadable `exp` is not cached; a token past `exp` is rejected.
3. 400/401/403/503, timeout, anonymous and unconfirmed responses are never cached (a later valid response is honoured).
4. Two accounts, two tokens: no cross-hits; a same-account refresh creates a new entry.
5. A revoked token is rejected once its entry lapses (a stub Auth server switching to 401).
6. LRU bound with more than 1,024 distinct tokens; memory structure holds no raw token (inspect keys and values).
7. Single flight: N concurrent requests produce one upstream call; a failing upstream call is shared and not stored.
8. Telemetry contains the new stages and no token, hash or UUID.
9. A real-Postgres run proving ownership scoping and `AccountUnavailable` still work with a cached identity.

**Expected benefit.** Skips all but the first Auth call per token per window: for a 10-minute lesson with a 60 s window, about 10 calls instead of every request. Saving per turn is `(requests per turn - 1/window share) x auth.verify`; decide only if measured `auth.verify` is at least about 100 ms per request and 0.3 s per turn.

## Option B (secondary): local ES256 verification
The project publishes a single **ES256** signing key at `/auth/v1/.well-known/jwks.json` (observed), so tokens can be verified offline with the public key. Steps: cache the JWKS (refresh at most every few minutes and on an unknown `kid`), verify signature and `alg` (reject anything but ES256), `iss` equals `<project>/auth/v1`, `aud` equals `authenticated`, `exp`/`nbf` with small skew, `role` is `authenticated`, `is_anonymous` false, `sub` a UUID.
* Saves **every** Auth call, including the first per token.
* Needs a **new dependency** (`PyJWT` with `cryptography`; neither is in `requirements.txt`), so it also needs the supply-chain and `pip-audit` review.
* The token has no `email_confirmed_at`: confirmed-email enforcement would depend on the project's "Confirm email" setting (to be verified in the dashboard) or on one Auth call per session.
* Revocation lag is the full token lifetime (Supabase default one hour; the project value is unverified), not 60 s.
* Key rotation and clock skew are new failure modes (fail closed, and fall back to the Auth call when the key set is unavailable).

## Comparison
| | A: 30-60 s cache | B: local ES256 |
|---|---|---|
| Calls removed | All but the first per window | All |
| New dependency | None | PyJWT + cryptography |
| Revocation lag | At most 60 s | Up to token lifetime (about 1 h) |
| Keeps confirmed-email and anonymous checks | Yes, unchanged | Needs dashboard assurance or per-session call |
| Complexity / risk | Low | Medium |
| Reversible | Env var, set 0 | Code + dependency removal |

## Recommendation
1. Measure first: wait for authenticated lessons and read `req.read.auth_verify`, `req.write.auth_verify` and `auth.verify` (p50 and p90, per turn).
2. If the cost is material, implement **Option A** behind `AUDLI_AUTH_CACHE_SECONDS`, default 0, as its own reviewed PR; enable in production only with separate approval, starting at 30 s.
3. Keep **Option B** as a follow-up only if A leaves a measured first-call cost that matters, and only after the dashboard checks and dependency review above.
4. Before either: run one revoked-token experiment against today's behaviour so the table above is fact, not assumption.
