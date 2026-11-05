# Audli 2.0 AI Lesson Generator (Milestone 05)

Learners describe a lesson in their own words ("Create a B1 listening lesson about ordering coffee in New York, with American accents, around three
minutes long") or use the structured controls. Both go through ONE pipeline; the settings are always shown for review before anything is generated.

```
text ─interpret job─► settings (topic, level, accent, minutes, format, scenario) ─► learner reviews / adjusts ─► generate job:
script (strict JSON) ► deterministic checks ► audio (one clip per sentence, measured cues) ► stored ► exercises ► published as a PRIVATE lesson
```

## Processes and credentials (no new service)
| Process | Database role | Holds | Never holds |
|---|---|---|---|
| API (`audli-api`) | `audli_api_runtime` | lesson-bucket sign key, signing secret | speech key, OpenAI generation calls |
| Spoken-answer worker (sidecar) | `audli_listening_worker` | OpenAI key, recordings bucket | lesson-bucket write key, ElevenLabs key |
| **Lesson generator (sidecar)** | `audli_listening_generator` | OpenAI key, ElevenLabs key, lesson-bucket write key | recordings credentials, signing secret, ops token |

Both sidecars are started by `scripts/start.sh` from explicit allow-lists (`app/listening/sidecar.py`). The generator claims only `interpret` and
`generate` jobs; the spoken worker claims only `spoken` jobs.

## What makes a lesson "ready"
Only the last transaction sets `phase = ready`, and only after: the script passed schema and deterministic checks (fill-in word occurs exactly once in
its sentence; sequence answer is the order of the evidence sentences; the multiple-choice answer shares content words with its evidence sentence more
than any wrong option; speakers, lengths and character limit); every sentence clip passed the audio gates and the cues were MEASURED on the final
timeline; the MP3 is stored and confirmed (`exists`); the exercises validated as a `LessonContent`. A failed request publishes nothing.

## Limits (all enforced atomically, before anything is sent; defaults in `app/config.py`)
* `AUDLI_LISTENING_GEN_PER_LEARNER_DAILY` (2) generated lessons per learner per UTC day.
* `AUDLI_LISTENING_GEN_DAILY_BUDGET_USD` (5.0): a SEPARATE ledger (`listening_generation_limits`); the $2/day assessment guard is untouched.
* `AUDLI_LISTENING_GEN_MONTHLY_CREDITS` (100,000): provider credits = characters x the model's multiplier (`eleven_flash_v2_5` 0.5, multilingual 1.0).
* `AUDLI_LISTENING_GEN_MAX_MINUTES` (2): longest lesson. Longer requests are shortened and the learner is told.
* Before any synthesis the provider's own remaining credits are read; fewer than the lesson needs (+200) means the request fails before spending.
* Reservation at request time, exact credits after the script is known, reconciliation to the provider's `character-cost` afterwards.
* `AUDLI_LISTENING_GEN_ALLOWED_LEARNERS` (empty = everyone the 2.0 allow-list admits): while set, only those learners can use any generation route; everyone else gets a plain 404. Used for the controlled production test.
* The global AI kill switch (`python -m app.listening.ai_budget halt`) pauses generation; queued jobs wait.
* Estimates are not billing. The ElevenLabs plan cannot be extended (no overage) and the OpenAI project limit remain the real backstops.

## Privacy
A generated lesson has a row in `listening_lesson_owners` and is visible only to its owner: catalog, lesson detail, attempts, and the request itself answer
404 to anyone else. The request text is stored with the request (not in logs). Failures show learner-safe text only, never provider messages.

## Enabling in production (order matters)
1. Backup and verify (`pg_dump -Fc`, restore into a scratch database, compare counts).
2. Apply `docs/postgres-012.sql` as the database owner; then `docs/postgres-listening-api-role.sql` again (new tables) and create the generator role
   (`docs/postgres-listening-generator-role.sql`).
3. Render `audli-api` environment: `AUDLI_LISTENING_GENERATION_ENABLED=true`, `AUDLI_LISTENING_GENERATOR_DATABASE_URL`, `ELEVENLABS_API_KEY`, and the
   limits above. Vercel: `NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR=true`.
4. Verify voices (`python -m scripts.verify_generation_voices --synthesize`), run one real generation, check the audio and the ledger.
Rollback: unset the two generation flags (nothing is deleted); migration 012 stays in place and is inert.
