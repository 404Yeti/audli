# AUD-77 / AUD-34: next approval package (refreshed 2026-10-23, after the production-copy rehearsal)

Three proposals. **Nothing here is approved or executed.** Each part ends with the exact approval it needs.

## Where things stand (2026-10-23, verified)

| Item | State |
|---|---|
| Production | API revision `2a2053c`; both Vercel apps serve Next.js 16.4.1; schema version 6; **migration 007 unapplied**; Review improvement flag **off** and account-deletion flag **off** (both inferred: the Render service exposes no environment values, and the app fails closed if the Review flag is on without 007); AUD-47 report OK; no paid provider call has ever run |
| PR #66, #67, #69 | merged and deployed (saved answer; flag-off coaching bridge; rejection recovery) |
| PR #72 (AUD-80) | merged `2d4a1e3`: Next.js 16.4.1, `npm audit` clean, Browser CI in a digest-pinned Playwright container |
| PR #70 | merged `aee7693`: live evaluation runner; **never executed** |
| PR #68 (AUD-34) | merged flag-off `5f5a3e6`; AUD-34 stays In Review; operational dependencies unresolved (part 2) |
| PR #74 | merged `cf218ee`: the 007 rollback refuses a populated table without an exact authorization phrase, plus the isolated rehearsal |
| PR #75 | merged `2a2053c`: read-only production backup, restore, rehearsal, verification and guarded-cleanup tooling; the prepared, **unapproved** live-evaluation approval file |
| PR #71 | closed, superseded by #72 |
| PR #73 | this document (draft) |

---|---|
| Production | API revision `aee7693`; both Vercel apps serve Next.js 16.4.1; schema version 6; **migration 007 unapplied**; Review improvement flag **off** (inferred from startup, which fails closed without 007; the Render service exposes no environment values); AUD-47 report OK; no paid provider call has ever run |
| PR #66, #67, #69 | merged and deployed (saved answer; flag-off coaching bridge; rejection recovery) |
| PR #72 (AUD-80) | merged `2d4a1e3`: Next.js 16.4.1, `npm audit` clean in `web/` and `site/`, Browser CI now runs in a digest-pinned Playwright container |
| PR #70 | merged `aee7693`: the live evaluation runner; **never executed** |
| PR #71 | closed, superseded by #72 |
| PR #68 (AUD-34) | rebased onto `aee7693`, net diff identical to the reviewed change (17 files); CI in progress; flag off; AUD-34 stays In Review |
| PR #74 | draft: 007 rollback now refuses a populated table without an exact authorization phrase, plus the isolated rehearsal (30/30; see part 3) |
| PR #73 | this document (draft) |

---

## 1. Synthetic live-model evaluation (PR #70, runner `scripts/run_review_improvement_eval.py`)

**Purpose.** Measure, on a real model, correction quality and meaning preservation of the Review coaching pipeline before any learner can use it. Mock tests prove plumbing only.

| Item | Value |
|---|---|
| Data | 55 synthetic ESL answers (`tests/fixtures/review_improvement_heldout.json`), disjoint from the development set; **no learner data, no database, no audio** |
| Model | `gpt-4.1-mini-2025-04-14`, pinned (an approval naming any other model is refused) |
| Prompt | `v2` (production prompt, production decision path) |
| Requests | 51 cases reach the provider (4 are blocked before any call). At most **153** requests (coach + one formatting repair + one comparison). Expected about 100 |
| Cost | Hard cap **$1.00** (a constant in code; an approval cannot raise it). Whole-plan worst case **$0.40**, expected about $0.02 |
| Ceiling method | Input reserved at 1 token per UTF-8 byte + framing; output at the hard cap (coach 1,000 / comparison 60). The run stops before a request that could cross the cap; usage above a reservation ends the run and is reported as an overrun |
| Network | No SDK retries, no resend after a failed or ambiguous request, concurrency 1, 30 s timeout, `store=false` |
| Durability | Request intent is fsync'd to a ledger before it is sent; an existing ledger blocks a rerun; nothing resumes |

**Test categories (counts):** grammar tense 3, agreement 3, articles 1, prepositions 2, word order 2, natural phrasing 5, vocabulary 4, disfluency 2, learner self-corrected 1, negation 4, modality 3, quantity 3, comparison 3, certainty 4, causality 3, named entity 3, plausible wrong fact 5 (the model must not "fix" a false statement), uncertain recognition 2, too short 1, too long 1. Each case lists reference rewrites, required fixes, words that must be preserved and words that must not appear (`docs/AUD-77-LIVE-EVALUATION-PLAN.json`, `expected_outputs`).

**Measured, per case.** Correction quality (best token-F1 to a reference, required fixes present). Meaning preservation (fixture invariants checked on the model's **raw** candidate, independent of the validator). Pipeline agreement: **FALSE_ACCEPT** (accepted but broke an invariant), false rejection, unneeded edit, and which validation layer (L1–L4) caught each violation. Cost (actual usage), latency p50/p95 per call, provider errors.

**Proposed pass criteria (for your decision, not yet adopted).** Zero FALSE_ACCEPT. Any false accept blocks enabling the feature and triggers a validator fix plus a new fixture. False rejections and unneeded edits are quality costs, reported with their rate. Cost under $0.10. A 55-case sample cannot prove a low rate, so the report states the sample size.

**What the owner does.** (1) Writes an approval file from the template in the plan (`approved: true`, run id, expiry, the model, prompt version, fixtures hash, prices, `max_usd` ≤ 1, `max_requests`). (2) Runs it with a key that has an organization-level spend limit of at most $5. (3) Receives `results.json`, `report.md` and the ledger.

**Data handled by OpenAI.** Synthetic text only. `store=false` is sent, but this is **not** zero retention (OpenAI keeps abuse-monitoring logs up to 30 days).

**Approval needed:** (a) PR #70 is already merged (the runner is inert); (b) execution still needs a separate approval file that you author. Nothing has been run.

---

## 2. Account-deletion release review (PR #68, AUD-34)

**State.** Draft, head `73bd876`, all 8 required checks green on that commit (before the Next.js advisories). Not merged, not deployed. Flag `AUDLI_ACCOUNT_DELETION_ENABLED` defaults off; with it off there is no delete route and the controls report it unavailable.

**What it does.** Schema-derived erase plan (reverse foreign-key order; only NOT NULL ownership paths; every table must be classified learner-owned or not, enforced by `tests/test_schema_ownership_inventory.py`). Covers `review_improvements`, `learner_roadmaps`, `homework_assignments`, `homework_reflections`, `lesson_entitlements`, and tolerates tables that do not exist (so it works at schema 6 and at 7). Locks `auth.users` to prevent resurrection, requires fresh password re-authentication, refuses accounts with verified MFA factors (support-assisted deletion only), revokes sessions, and never reports success on partial failure.

**Test evidence.** Independent SQL oracle seeds every learner-owned table for two learners and verifies ordering, FK constraints, isolation (the other learner is untouched) and complete removal, on SQLite and PostgreSQL.

**Not solved by the code (each must be resolved or explicitly accepted before the flag is turned on):**

| Dependency | Why it matters | Needed |
|---|---|---|
| Provider copies (AUD-36) | OpenAI keeps abuse-monitoring logs up to 30 days; ElevenLabs history is not purged by this PR | Documented retention exceptions in the privacy notice, or a purge path |
| Backups and restore | A restore can resurrect a deleted learner; there is no deletion ledger | A re-apply-deletions step in the restore runbook, or an accepted retention window |
| MFA accounts | Self-service deletion refuses accounts with verified MFA factors and points them to a support contact | A support process for those learners |
| Deployed environment | `auth.*` privileges for the service role, zero storage buckets, single instance | A read-only confirmation on the deployed project |
| Legal holds | Deletion must not destroy data under a hold | An owner statement that no hold applies, or a hold check |

**Proposed sequence.** (1) Merge #68 as a **flag-off** release (inert) once CI is green on its final head; this is already approved. (2) Resolve or accept each row above. (3) Separate approval to enable the flag, with a first deletion of an owner-controlled test account only.

**Approval needed:** (a) merge #68 flag-off after CI is green; (b) later, a written decision on each dependency row; (c) later, enabling the flag.

---

## 3. Migration 007 backup and rehearsal: DONE on a restored copy of production

**Result (2026-10-23): production backup and complete rehearsal PASSED; the temporary copy has been deleted; production was only read.** Sanitized report (counts, checksums and pass/fail only): `~/.config/audli/migration007/reports/20261023T043435Z-sanitized-report.json` on the operator machine.

| Check | Result |
|---|---|
| Target | production fingerprint `6828eddfe138` = the target of the earlier approved 006 runs; port 5432; PostgreSQL 17.11; session reported `default_transaction_read_only = on` |
| Preflight | PASS: schema exactly 1-6, `review_improvements` absent, no long transactions |
| Backup | one exported REPEATABLE READ READ ONLY snapshot used by `pg_dump` and by the counts and checksums; 35,561,197 bytes, sha256 `11e2d2c0...fe63b`, 157 TOC entries, 25 tables |
| Restore | into a new database on an isolated loopback server: all 25 table counts, lesson statuses, lesson checksum (`96fd2917...`), entitlement checksum (`86a95ecc...`) and schema versions identical to the snapshot |
| Isolation | server listens on 127.0.0.1 only; its environment held no production variable; the dump has 0 objects that could link outward; only `plpgsql` installed; 0 foreign servers, user mappings, foreign tables, subscriptions |
| Production counts (snapshot) | users 9, lesson_lifecycles 25 (active 1, paused 1, completed 23), lesson_entitlements 7, exercises 49, attempts 143, evaluation_results 47, learner_roadmaps 5, homework_assignments 13, homework_reflections 6 |
| Rehearsal on clones of the restored copy | **31 of 31 passed**: 007 applies in 0.04 s; every existing table identical by md5 (incl. lesson_lifecycles and lesson_entitlements) with only the version-7 row added; columns, RLS and privileges; constraints; atomic re-apply; **ownership isolation** (another learner cannot read or claim an exercise they do not own); rollback refuses a populated table three ways, succeeds with the exact phrase, discarded rows recoverable from a table backup, empty table needs no phrase; pre- and post-007 backups restore exactly; the app fails closed with the flag on at schema 6; the source copy proven unmodified |
| Cleanup | guarded script ran only after the report was saved and posted: restored database, dump, run folder, the isolated server's data and log (140 identifier-shaped strings from the deliberate constraint tests) and the rehearsal scratch removed; the existing backups folder, the 006 run folder and old rehearsals are byte-for-byte unchanged (6,415 files compared) |

**Limits.** Rehearsal ran on PostgreSQL 17.10 (production 17.11), not Supabase's pooler; production data is small (9 users); lock behaviour under live traffic is not exercised, but 007 only creates a new table and takes no lock on existing tables. The older 006 run folder still holds a production dump from 2026-10-22; its retention is the owner's call.

**Before applying 007 to production (separate approval):** a fresh backup immediately before applying (this one is deleted and production keeps changing); the 006-style apply gates (fingerprint, file hash, exact confirmation phrase, backup/restore/rehearsal manifests younger than 24 h); and the decisions below. The application at `2a2053c` already accepts schema ledgers [1..6] and [1..7], so applying 007 needs no deploy and does not enable anything.

### Procedure (unchanged)

**What 007 is.** One additive table `review_improvements` with RLS on, access revoked from PUBLIC/anon/authenticated, one index and one partial unique index, plus the `schema_migrations` row for version 7. No existing table changes. File: `docs/postgres-007.sql` (sha256 begins `79a5291130179…`), rollback `docs/postgres-007-rollback.sql`. The application at `cf9de1c` already accepts schema ledgers [1..6] and [1..7], so applying 007 needs **no deploy**. The feature itself fails closed if its flag is on without the table.

**Method: reuse the proven migration-006 toolchain** (`scripts/migration006/`, operator guide `docs/AUD-47-MIGRATION-006-OPERATOR-GUIDE.md`), adapted into `scripts/migration007/` in a PR of its own:

1. **Preflight (read-only, server refuses writes):** schema versions exactly 1–6, `review_improvements` absent, no long transactions or conflicting locks, baseline counts, target fingerprint, sha256 of the reviewed file.
2. **Backup:** one consistent `pg_dump -Fc` snapshot of schema `public` with counts from the same snapshot, size, sha256, table of contents.
3. **Restore-verify (isolated local PostgreSQL 17):** restore into a fresh database and compare every table count, key checksums and schema versions with the snapshot.
4. **Rehearse on the restored copy:** preflight → apply 007 with production timeouts (`lock_timeout 5s`, `statement_timeout 30s`) → post-checks → app smoke test with the flag on and off → rollback → verify the original state.
5. **Post-checks:** versions exactly 1–7; table columns and constraints exact; RLS on; `anon`/`authenticated`/PUBLIC have no access; the partial unique index allows one live row per exercise; **no existing table changed** (counts and checksums equal).
6. **Apply gates (production):** fingerprint match, file hash equals the reviewed hash, exact confirmation phrase, backup/restore/rehearsal manifests PASSED and younger than 24 h, fresh read-only preflight. One transaction; any failure leaves production unchanged. No script restores or rolls back automatically.

**Risk.** Low: additive, transactional, nothing reads the table while the flag is off. Rollback (`docs/postgres-007-rollback.sql`, manual only) drops the new table and the version-7 row unconditionally; it removes only derived advisory text, never saved answers, scores or history, but it does not check that the table is empty. If the feature has ever been enabled, take a table-level dump first.

**Not part of this approval:** applying 007 and enabling the flag are separate. Enabling additionally requires the retention decision below.

**Approval needed:** (a) applying 007 to production, separately, with a fresh backup and the exact confirmation phrase; (b) separately, enabling `AUDLI_REVIEW_IMPROVEMENT_ENABLED`.

---

## Decisions that gate enabling corrections (not yet made)

- **Provider retention.** The learner-facing disclosure (`docs/AUD-77-LEARNER-DISCLOSURE.md`) says answer text is sent to an AI provider, that voice is not, and does not claim zero retention. Zero retention can be claimed only after the organization-level controls are verified.
- **Live evaluation result** meeting the pass criteria above.
- **AUD-34 dependencies** are not required for the Review feature, but the privacy notice must not promise deletion of provider-held copies.

## Order I recommend (remaining)

1. Live evaluation: fill in and approve `docs/AUD-77-LIVE-EVALUATION-APPROVAL.json` yourself (it ships with `approved: false` and your four fields blank), run it from a shell with no database variables and an API key with an organization spend limit. Pass criterion proposed: zero false accepts.
2. Decide provider retention terms; do not claim zero retention without verified organization-level controls.
3. Apply 007 to production only after steps 1 and 2, with a fresh backup immediately before.
4. Separately decide on enabling the Review improvement flag (and, much later, account deletion after the AUD-34 dependencies).
