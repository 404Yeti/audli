# Audli 2.0 AI safeguards: spending ceiling, kill switch, disclosure (AUD-100)

## What they are, in one paragraph
Every Audli 2.0 AI request (transcription, assessment, and any future lesson generation) must first **reserve its worst-case cost** against a daily
ceiling kept in the database. The reservation is one conditional `UPDATE`, so any number of workers can never reserve past the ceiling, and a
request that does not fit is **never sent**. Anything that prevents the check (unknown model price, implausible estimate, database error) refuses the
request: the guard fails closed. A kill switch stops new requests immediately. The recording screen tells the learner where their audio goes.

## The ceiling (`app/listening/ai_budget.py`, table `listening_ai_limits`, migration 011)
* `AUDLI_LISTENING_AI_DAILY_BUDGET_USD` (default **2.00**, per UTC day, across every 2.0 AI operation). Stored as integer millionths of a dollar.
* **Estimates are worst cases**: transcription reserves 60 s at the model's per-minute price (US$0.0045 for `gpt-transcribe`, 4,500 micro-dollars); text
  requests reserve (input characters ÷ 2.5 + 300 tokens) at the input price plus the *whole* output cap at the output price (hidden reasoning counts as output).
  A model without a price in `app/listening/models.py` cannot be used until one is added. One request may never reserve more than US$0.25.
* **Settlement:** after a call, the reservation is replaced by the cost computed from the usage the provider reported. A request the provider refused outright
  (4xx) is released. A timeout or 5xx stays reserved, because it may still have been billed. A settlement problem never breaks the learner's flow; the higher number stays.
* **Concurrency:** the conditional update is atomic on PostgreSQL and SQLite; tests run 16 simultaneous reservations against room for exactly 3 and get exactly 3.
* **Uploads:** while halted, or when under US$0.01 of today's ceiling is left, the API refuses a new spoken answer (`503`, "Spoken answers are paused for a little
  while. You can skip this question…") before storing anything, and the same for *Check again*. In-flight jobs ask the guard again; a refusal retries with backoff
  (15 s, 30 s, 60 s) and then the answer shows as unavailable, with the learner's words (if any) kept for a later retry.
* Development with the fake providers spends nothing, so only the kill switch applies there.

## The kill switch
| How | Effect | Needs a deploy? |
|---|---|---|
| `python -m app.listening.ai_budget halt` / `resume` / `status` | the database flag; the next request anywhere is refused / allowed | no |
| `AUDLI_LISTENING_AI_ENABLED=false` | the process refuses all AI work and uploads | restart |
Halting stops *new* requests; a request already on the wire finishes and settles. `status` prints today's reserved amount, calls, ceiling and halt state (no secrets).
If the database cannot be read, the system treats AI as halted.

## What this is not
**Reserved amounts are estimates, not billing.** They protect against bugs, retry storms and runaway loops inside Audli. They do not see other uses of the same
key, price changes, usage the provider reports late, or the exact invoice. **Keep a spend limit on the OpenAI project that owns the key** (and an alert); that is
the real backstop and the only control that works if this code is wrong or bypassed. Review the price table in `app/listening/models.py` whenever a model changes.

## The disclosure (recording screen)
Shown before recording and again when the learner is about to send (`data-testid="provider-disclosure"`, constant `PROVIDER_DISCLOSURE`):
"Your spoken answer is sent to an external AI provider (OpenAI) to turn it into text and check it. The temporary audio is deleted as soon as it has been
transcribed; the text stays in your summary." If the provider changes, change the sentence in the same commit. The privacy policy and OpenAI's own retention
terms for the API still need a human review (not done here).

## Rollout
1. Apply migration 011 after 009 and 010: `python -m scripts.migrate_postgres --listening`. The API and worker roles need SELECT, INSERT and UPDATE on `listening_ai_limits`.
2. Set `AUDLI_LISTENING_AI_DAILY_BUDGET_USD` deliberately (US$2 covers roughly 400 answers a day at current estimates) and the provider-side limit.
3. Practise `halt` and `resume` on staging before the first learner.
