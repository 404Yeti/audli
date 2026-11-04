# Audli 2.0 listening lessons (Milestone 01)

One saved, reusable B1 listening lesson with private audio, a synchronized optional transcript, a multiple-choice question,
deterministic feedback and a recoverable attempt. It runs beside the 1.x tutor and shares nothing with its conversation engine.

## What a learner can do
Sign in → `/listening` → open **Ordering food at a restaurant** (B1) → play, seek, skip ±10 s, change speed, replay →
optionally switch to **Guided** to follow the transcript line by line (tap a line to replay it) → **Start exercises** → answer →
see whether it was right and why → refresh at any point and pick up where they left off.

## Architecture
```
Next.js /listening ──Bearer──▶ FastAPI /api/listening/*  ──▶ PostgreSQL (SQLite locally)
        │                              │ ownership check
        └─ <audio src=signed URL> ◀────┴─ ObjectStore.signed_url()  ──▶ S3-compatible bucket (local dir in dev)
```
No provider, no tutor, no lock. `app/listening/`: `domain.py` (pure validation and scoring), `schema.py` (tables + immutability
triggers), `store.py` (queries), `api.py` (routes and response models), `objects.py` (object storage), `seed.py` (seed loader),
`migrations.py` (generates `docs/postgres-009.sql`).

### Tables (migration 009)
`listening_lessons` · `listening_lesson_versions` (immutable once `published`) · `listening_exercises` · `listening_exercise_keys`
(private) · `listening_audio_variants` (append-only; per-accent alignment JSON; content-addressed `object_key`) · `listening_attempts`
(pins `version_id` and `audio_variant_id`) · `listening_responses` (composite foreign keys force the answered question to belong to
the attempt's version) · `listening_migrations`. Learner rows cascade from `users`, so the unchanged 1.x erasure plan removes them.
RLS is enabled and `PUBLIC`/`anon`/`authenticated` are revoked, like every earlier migration.

Database triggers refuse: any change to a published version, its exercises or keys; any update or delete of an audio variant; any
update of a response; any change to an attempt's identity or a completed attempt. The application never needs to do these.

### Endpoints (all under `/api/listening`)
| Method and path | Purpose |
| --- | --- |
| `GET /lessons` | Published lessons (latest version each): title, CEFR, accents, duration |
| `GET /lessons/{lesson_id}/attempts/current` | The learner's open attempt, else their latest finished one, else `null` |
| `POST /lessons/{lesson_id}/attempts` | Idempotent by `client_request_id`; resumes an open attempt instead of duplicating |
| `GET /attempts/{attempt_id}` | Recover an attempt: lesson, transcript, alignment, questions, feedback for answered ones |
| `POST /attempts/{attempt_id}/answers` | One final answer per question; identical retry is a replay, a different answer is `409` |
| `GET /attempts/{attempt_id}/audio` | Short-lived (5 min) signed playback URL for the attempt's audio variant |
| `GET /media/{key}?exp&sig` | Local development only: serves a signed file with Range support (no bearer token by design) |

## Run it locally
```bash
.venv/bin/pip install -r requirements.txt && (cd web && npm install)
AUDLI_LISTENING_ENABLED=true .venv/bin/python -m scripts.seed_listening_lesson      # publishes the seed lesson (idempotent)
AUDLI_LISTENING_ENABLED=true .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
cd web && npm run dev                                                                   # http://127.0.0.1:3000/listening
```
Local mode needs no Supabase. With `AUDLI_AUTH_MODE=supabase` the same routes require a verified, confirmed Auth user.

## Tests
```bash
.venv/bin/pytest -q tests/test_listening.py                       # SQLite
AUDLI_TEST_POSTGRES_URL=postgresql://user:pw@127.0.0.1:5432/postgres .venv/bin/pytest -q tests/test_listening.py   # + real PostgreSQL (disposable cluster only)
cd web && node --experimental-strip-types ../tests/listening.test.mjs
cd web && npx playwright test -c listening.playwright.config.ts --project=chromium   # real API + SQLite + browser
```

## Production checklist (not done by this change)
1. Deploy code first (feature flag off): nothing changes for 1.x.
2. Apply migration 009 as the backend database owner: `python -m scripts.migrate_postgres --listening`. It requires 001–007, refuses to
   rerun, and does not touch `schema_migrations`.
3. Create a **private** S3-compatible bucket and a key scoped to that bucket. Set `AUDLI_LISTENING_STORAGE=s3`,
   `AUDLI_LISTENING_S3_ENDPOINT` (https), `_BUCKET`, `_REGION`, `_ACCESS_KEY`, `_SECRET_KEY`. Production refuses to start otherwise.
4. Publish the lesson (`python -m scripts.seed_listening_lesson`) with a **real** voice recording. The checked-in audio is a robotic
   espeak-ng development fixture, deliberately not a quality benchmark.
5. Set `AUDLI_LISTENING_ENABLED=true`.

## Design decisions worth knowing
- **Own migration ledger.** `app/claims/runtime.py` treats `max(schema_migrations.version) >= 8` as "claims schema present" and refuses to
  start without claims mode, and migration 008 asserts the ledger is exactly 1–7. Recording 009 there would break 1.x startup and 008,
  so 009 records itself in `listening_migrations`. If you later want one ledger, change that gate deliberately and separately.
- **Content-addressed audio.** Versions with identical audio share one object; the store refuses a key reused with different bytes.
- **Alignment is measured, not estimated**, for the fixture: each sentence is synthesized separately and the offsets are exact.
  Provider audio will need its own alignment source (timestamps from the provider or per-sentence synthesis); that is not built.
- **Signed URL expiry** is handled in the player: on an audio error it fetches a fresh link once and resumes from the same position.
- **Account deletion** is unchanged and complete for learner rows. It does not enumerate these tables; it relies on `ON DELETE CASCADE`.

## Known limitations
Only the multiple-choice step exists. No spoken response, fill-in, sequencing, debrief or success screen, no catalog search or
recommendations, one accent, one lesson. Local JWT verification was deferred (1.x still calls Supabase per request). Real S3
transport and WebKit/iPhone Safari were not exercised; see the pull request for exactly what was and was not verified.
