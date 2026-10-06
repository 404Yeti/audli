# Audli
Audli is an adaptive English listening-comprehension tutor. Train your ears.

## Architecture
Next.js/TypeScript client; FastAPI/Pydantic API; SQLite local persistence behind a repository. PostgreSQL reference schema lives in docs. Provider-specific generation, evaluation, speech and transcription live in app/services. Application code owns state, scores, adaptation and history.

## Product rules
- AUDIO FIRST, TEXT SECOND. Never expose scripts or expected answers before a successfully evaluated spoken attempt. Enforce this on the server, including debug endpoints.
- Evaluate meaning, not grammar or pronunciation. Distinguish missed from misunderstood information.
- Preserve STT uncertainty. Unreliable attempts must not change learner state.
- Change at most one primary difficulty variable. Maintaining difficulty within the learning edge or at bounds is valid. Persist explainable decisions.
- Original English only, one clear speaker. No external media, agents, payments, gamification or advanced difficulty dimensions.
- No client secrets. Validate audio size, type, signature, decodability and duration. Delete learner audio after transcription; no raw audio logging.
- Local single-user prototype. Public deployment requires authentication and access controls.

## Testing and conventions
Use strict typed schemas, small pure domain functions, explicit transactions and provider interfaces. Tests must cover .54/.55/.69/.70/.84/.85 boundaries, one-variable changes, bounds, state updates, schema rejection, uncertainty and transcript gates. Evaluator fixtures must include poor grammar with correct meaning. Mock tests establish plumbing, not actual evaluator accuracy.

## Commands
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
.venv/bin/pytest -q
cd web && npm install && npm run dev
cd web && npm run build
docker compose up --build

## V0.2 spoken evidence loop
- Keep the v0.1 tag unchanged; reuse its generation repair, audio validation and recording helpers.
- Insufficient evidence is unknown, never a confirmed zero. Persist it separately from misunderstanding.
- Ask at most two voice follow-ups, only for insufficient evidence; choose progression in application code.
- Adapt only after the final assessment. If evidence remains incomplete, keep difficulty stable and audit why.
- Persist conversation turns/checkpoints. TTS failure must never roll back a completed assessment.
- Keep transcript gates in the backend; legacy evaluation cannot bypass an active V0.2 conversation.
- Spoken cues also have accessible text and replay controls; detailed metrics stay secondary.

## V0.2.1 terminal feedback
- Follow-up questions, evaluator notes and final feedback are separate conversational acts.
- Never speak evaluator notes directly. Compose terminal feedback from final evidence and the deterministic adaptation event.
- Validate the complete spoken message: at most three sentences and configured 30–50 word maximum; no questions, requests for evidence or numerical metrics.
- Unknown units never supply corrections. Unsafe/oversized correction labels use a complete safe alternative, not truncated prose.
- Version terminal-audio caches so previous evaluator-report audio cannot replay.
