# Curator batch workflow: fill the library from a manifest

`python -m app.listening.curate` turns the reviewed manifest `docs/library-manifest.json` (the 20-lesson launch set) into private curator drafts, one lesson at a time, through the same structured pipeline a learner uses. It cannot publish anything: review, register, approve and publish stay in `python -m app.listening.library`. Nothing here is executed by merging; paid generation starts only when the owner runs `submit`.

## How it is kept safe
| Concern | How |
|---|---|
| Who may run it | The account must be on `AUDLI_LISTENING_CURATOR_IDS`, and the run uses the operator's database credentials (same as the library CLI). `--approve-usd` must equal the stage budget in the manifest. |
| Learner behaviour | Unchanged. Ordinary accounts keep 2 lessons/day. The curator allowance (`AUDLI_LISTENING_GEN_CURATOR_DAILY`, 1-5, default unset = ordinary cap) applies only to listed accounts and is read by whichever process reserves, i.e. this CLI. The Render learner API does not need it. |
| Limits | `Ledger.cap_for` is used by both `availability` and the atomic `reserve` (per-account advisory lock on PostgreSQL, write lock on SQLite). The global daily USD ceiling, monthly credits and the AI kill switch are checked in the same reservation and always win. |
| Spend cap | Per stage, cumulative and durable: before every submission the CLI sums the stored requests of that stage (reservations; settled lessons at their real cost; failed attempts at one script call plus speech used) and refuses if that plus the next lesson's worst case would pass the stage budget. There is no counter to reset; restarts, retries and partial runs all see the same rows. Stages other than the approved ones have budget 0. |
| Idempotency | `client_request_id = curate:<manifest>:<stage>:<key>:r<attempt>`. A rerun never creates a second request. A request refused for a limit (nothing reserved) is resumed in place next time. One submit runs per manifest at a time (PostgreSQL session lock). |
| Failures | Never retried automatically. The run stops and tells you; `--retry KEY` starts attempt r2 after you have looked, and the failed attempt still counts against the budget. |
| Privacy | Generated lessons are owned by the curator account (no shared visibility) until `publish`. Output shows keys, ids, phases and dollars only: no credentials, tokens or connection strings. |

## Configuration the operator session needs
Export these in the shell that runs the CLI (values below are what the pilot needs):
```
AUDLI_PERSISTENCE=postgres
AUDLI_DATABASE_URL=...                      # the owner Session Pooler URL: type it with read -rsp, never paste it anywhere
AUDLI_LISTENING_ENABLED=true
AUDLI_LISTENING_GENERATION_ENABLED=true
AUDLI_LISTENING_CURATOR_IDS=<your curator account id>
AUDLI_LISTENING_GEN_CURATOR_DAILY=5
AUDLI_LISTENING_FORMATS_ENABLED=true
AUDLI_LISTENING_FORMAT_ACCENTS=canadian,japanese,scottish,indian
```
The **generator process on Render must allow the same accents and formats** (`AUDLI_LISTENING_FORMATS_ENABLED`, `AUDLI_LISTENING_FORMAT_ACCENTS`), otherwise the job fails with `format_unavailable` and nothing is saved. That is a production configuration change that needs the owner's approval; `plan` cannot see Render's settings.

## Procedure
1. `python -m app.listening.curate plan --stage pilot --curator <id>`: read-only; lists blocked accents/formats, estimates, allowance, stage budget. Fix everything it reports first.
2. `python -m app.listening.curate submit --stage pilot --curator <id> --approve-usd 1.00`: submits ONE lesson (`--limit N` for more) and exits. The generator process builds it.
3. `python -m app.listening.curate status --stage pilot`: wait for `ready`.
4. Review it in the app signed in as the curator (the 10 checks in `LISTENING-LIBRARY-LAUNCH.md` section 3).
5. Smoke and privacy check BEFORE publishing (two signed-in sessions, read-only): `python scripts/listening_smoke.py draft <lesson_id>` (see its header for the tokens).
6. `python -m app.listening.library register --category C <id>`, then `approve --reviewer ... --note ...`, then `publish`.
7. `python scripts/listening_smoke.py published <lesson_id>`: the other account now sees and opens it.
8. Repeat from step 2 (the first lesson gates the rest). Raising a later stage's budget is a reviewed change to the manifest.
