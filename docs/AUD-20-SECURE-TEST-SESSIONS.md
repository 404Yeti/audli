# AUD-20 production acceptance access

The protected production database file is available and owner-only. Its URI
matches the intended production session pooler and requires TLS. Explicit
read-only PostgreSQL access was verified on 2026-10-11: server 17.11, migrations
001/002, no accent_control table, no missing profile/audio owners and no
unvalidated public constraints. Aggregate row counts were stable across the two
read-only inspections. This checks present integrity; without a historical
private snapshot it does not prove that every historical row is unchanged.
See [AUD-20-ACCESS-VERIFICATION.json](AUD-20-ACCESS-VERIFICATION.json).

No two approved production test sessions or test-account credentials were found
in the approved local configuration directory, project dotenv files or process
configuration. No browser storage, Auth session tables or password recovery was
used to obtain credentials. The latest product-owner instruction explicitly accepts deferring the dedicated two-account authenticated production test. This procedure is optional follow-up; absent sessions are no longer a release blocker. Production cross-account playback/isolation remains unverified, and existing authentication/authorization controls remain mandatory.

## Small secure session procedure

Run this owner-operated helper from the repository in your interactive terminal:

```sh
.venv/bin/python /tmp/aud20-create-test-sessions.py
```

The helper is outside the repository and contains no credentials. It prompts
without echo for the **production public `sb_publishable_` key**, then the email
and password for each of **two distinct, existing, authorized test accounts**.
Use the publishable key from the existing project's API-key settings, never a
service-role or secret key. It uses normal password sign-in and Auth's user
verification; it never registers accounts, fabricates tokens, reads browser
storage, requests confirmation bypass, or invokes Audli lesson/provider routes.
If the accounts use SSO/MFA or password sign-in is unavailable, stop and arrange
the normal supported sign-in procedure rather than bypassing it.

The existing `~/.config/audli` directory has mode 700. On success the helper
creates `~/.config/audli/test-sessions.json` with mode **600**, refusing to
overwrite an existing file or follow an output symlink. It stores only account
labels, verified account IDs, access tokens and expiration metadata. Passwords,
email addresses, publishable key and refresh tokens are not persisted. It
requires access-token lifetimes of at most one hour and never changes production
Auth expiration settings. Normal sign-in creates Auth session records and may
update last-sign-in metadata; it does not change learner progress or assessments.
The helper passed syntax checking only; real sign-in remains untested because
the owner has not supplied the test credentials. Do not treat that as acceptance.

After running it, reply with the private file path **only**. Do not send its
contents, passwords, tokens or a browser-storage export. Run shortly before
acceptance because access-only tokens expire and cannot refresh automatically.
Remove the private token file when acceptance finishes; do not add it to git.

Before authenticated API checks, verify read-only that both identities already
have Audli user/profile records and stored passage/tutor audio. If a profile is
missing, an authenticated request would initialize learner data: stop and obtain
explicit authorization before making that request. If saved audio is absent,
stop rather than generating new paid audio or modifying progress. Use existing
owned passage GET and saved coach-audio GET routes only; verify each other
account receives 404. Audio restoration may populate ephemeral server cache
files but does not alter learner progress. Never call generation, assessment,
lesson-advance or synthesis endpoints for this acceptance without authorization.

Any later execution must respect the accepted verification gap and current active global release authorization. The minimum post-003 rollback remains bridge `2454f04`.
