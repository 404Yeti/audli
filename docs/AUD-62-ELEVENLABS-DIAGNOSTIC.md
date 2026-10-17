# Temporary ElevenLabs verification (no paid Render shell)

This diagnostic uses the existing Render environment and sends one `Hello.` TTS
request to the configured ElevenLabs voice/model. It has no endpoint, database
access, provider fallback, retry or audio storage. It runs off the event loop;
startup and health checks do not wait for it. Default: no task, request or log.
Only HTTP status and a fixed category are logged. Remove the module, its lifespan
hook and tests after verification; remove the two temporary variables as well.

## Controlled execution

1. Let the existing eight required CI checks pass and review/merge PR #44 through
   branch protection. Its normal deployment leaves this diagnostic disabled.
   Do not change the existing ElevenLabs key, voice/model, homework settings or
   database. Confirm Render's production service has **one instance**; if more
   than one, stop rather than run this diagnostic on multiple instances.
2. Copy the **full 40-character deployed merge commit** from Render's deployment
   details (not the PR head). In the existing Audli backend's Render **Environment**
   page, add these service-level variables using **Save only**:

   ```text
   AUDLI_ELEVENLABS_DIAGNOSTIC_ENABLED=true
   AUDLI_ELEVENLABS_DIAGNOSTIC_REVISION=<full deployed merge commit>
   ```

   These are switches, not credentials. Never paste or change `ELEVENLABS_API_KEY`.
3. Use **Manual Deploy → Deploy latest commit** only if latest is that exact
   approved merge revision. If main has advanced, stop and select/verify the
   intended revision instead. Perform one controlled deployment. A mismatched
   revision intentionally sends no request.
4. In Render **Logs**, filter for `ElevenLabs diagnostic`. Expect one line:

   ```text
   ElevenLabs diagnostic http_status=200 category=accepted
   ```

   This confirms authenticated synthesis acceptance for the configured custom
   voice/model. It does not establish audible voice identity or iPhone playback.
   Successful audio is immediately discarded, not saved or logged. The six
   characters can consume a small amount of existing ElevenLabs credit.
5. Immediately set `AUDLI_ELEVENLABS_DIAGNOSTIC_ENABLED=false` and remove
   `AUDLI_ELEVENLABS_DIAGNOSTIC_REVISION`, then **Save, rebuild & deploy** (or the
   equivalent save-and-deploy option). Confirm production health and no diagnostic
   line in the new deployment's logs. Do not retry the enabled deployment.
6. Record only the sanitized result in PR #44. Remove temporary code in a small
   follow-up PR with the usual required CI checks. No schema migration is needed.

The exclusive `/tmp` marker prevents repeated startup/worker execution in the
same container, even on failure. **Render can replace ephemeral containers:**
restarts, replacement instances or redeployments while the flag remains enabled
can each issue another request. This is not a distributed exactly-once system.
Use one instance and one controlled deployment, disable immediately, and do not
leave the switches enabled. The revision pin prevents subsequent different
revisions from probing. No paid hosting upgrade or persistent database marker is
required.

## Result → smallest corrective action

| Result | Action |
|---|---|
| `200 / accepted` | Authentication and configured synthesis are working now; historical401 cause may remain unknown. Proceed with release decision; owner tests audible identity on iPhone after deployment. |
| `401 / invalid_api_key` | Owner verifies the existing Render key against the intended ElevenLabs workspace. Replace only if confirmed invalid/revoked, through Render's secret workflow. |
| `quota_exceeded` | Owner checks existing ElevenLabs credit/key spending limit and restores available allowance if desired. Do not rotate keys to evade limits. |
| `missing_permissions` | Owner grants the existing key the required text-to-speech permission and access to the configured voice. |
| `paid_plan_required` | Owner checks voice/model entitlement. Report the requirement; do not automatically upgrade any service. |
| `voice_not_found` | Owner verifies custom voice access in the existing ElevenLabs workspace. Preserve the intended voice ID. |
| `unusual_activity` | Owner resolves the provider restriction through ElevenLabs. Do not retry repeatedly. |
| `missing_credentials` | Restore the existing intended `ELEVENLABS_API_KEY` through the secure Render environment workflow. |
| `network_failure` or `provider_rejected` | Report the sanitized status; do not infer a credential problem or automatically retry. Further diagnosis requires a specific next step. |

No log means disabled/mismatched revision, an existing marker, or unavailable
marker storage; it is **not** a successful provider verification. No normal
request, learner record, lesson completion, fallback behavior, homework quota,
YouTube configuration or schema005 is changed by the diagnostic.
