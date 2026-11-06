"""Learner API for 2.0 listening lessons. No AI provider, no conversation state and no process-wide lock: concurrency control is
the database's (unique constraints, a row lock on the attempt, idempotent writes)."""
import logging
import time
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.accent_catalog import ACCENT_IDS
from app.listening import spoken, store
from app.listening.ai_budget import AiGuard
from app.listening.recordings import RecordingRejected, validate_recording
from app.listening.domain import Cue
from app.listening.objects import LocalObjectStore

SIGNED_URL_SECONDS = 300
REQUEST_ID = r'^[A-Za-z0-9_-]{8,64}$'


class CatalogItem(BaseModel):
    lesson_id: str
    slug: str
    title: str
    topic: str
    cefr: str
    listen_for: str
    accents: list[str]
    duration_ms: int
    # Where this learner is with the lesson (filled in per account; a lesson nobody has opened is simply "new").
    status: str = 'new'                  # new | in_progress | completed
    attempt_id: str | None = None
    answered: int = 0
    total: int = 0
    correct: int | None = None           # among the scored questions, finished attempts only
    scored: int | None = None
    spoken_status: str | None = None     # pending | assessed | failed | skipped | None
    last_activity_at: datetime | None = None
    completed_count: int = 0


class RecentOut(BaseModel):
    attempt_id: str
    lesson_id: str
    title: str
    cefr: str
    status: str
    when: datetime
    correct: int | None
    scored: int | None
    spoken_status: str | None


class ProgressOut(BaseModel):
    lessons_available: int
    lessons_completed: int
    attempts_completed: int
    questions_correct: int
    questions_scored: int
    accent_counts: dict[str, int] = {}        # distinct lessons finished, per accent heard
    level_counts: dict[str, int] = {}         # distinct lessons finished, per CEFR level
    recent: list[RecentOut]


class AudioInfo(BaseModel):
    id: str
    accent: str
    duration_ms: int
    cues: list[Cue]


class Speaker(BaseModel):
    id: str
    name: str
    role: str
    accent: str | None = None            # the accent this speaker is voiced in (lessons made after per-speaker accents); a catalog id, not an identity


class SentenceOut(BaseModel):
    index: int
    speaker: str
    text: str


class LessonOut(BaseModel):
    lesson_id: str
    version_id: str
    version_no: int
    title: str
    topic: str
    cefr: str
    listen_for: str
    speakers: list[Speaker]
    sentences: list[SentenceOut]


class OptionOut(BaseModel):
    id: str
    text: str


class Feedback(BaseModel):
    selected_option_id: str
    is_correct: bool
    correct_option_id: str
    explanation: str


class FillResult(BaseModel):
    answer: str
    is_correct: bool
    accepted: str
    explanation: str


class SequenceResult(BaseModel):
    order: list[str]
    is_correct: bool
    correct_order: list[str]
    positions_correct: int
    explanation: str


class PointOut(BaseModel):
    id: str
    text: str
    state: str
    quote: str | None


class SpokenResult(BaseModel):
    """The state of this learner's spoken answer. Key points appear only once an assessment exists; nothing here is a score."""
    status: str                          # pending | assessed | failed | skipped
    retryable: bool
    transcript: str | None
    insufficient: bool
    points: list[PointOut] | None
    covered: int | None
    partly: int | None
    total_points: int | None
    failure_code: str | None


class ExerciseOut(BaseModel):
    id: str
    position: int
    kind: str
    prompt: str
    options: list[OptionOut]
    feedback: Feedback | None = None   # present only after this learner answered this question
    fill: FillResult | None = None
    sequence: SequenceResult | None = None
    spoken: SpokenResult | None = None


class AttemptOut(BaseModel):
    id: str
    status: str
    total: int
    scored: int                  # exercises scored by application code (everything except the spoken answer)
    correct: int | None          # correct among the scored exercises only
    created_at: datetime
    completed_at: datetime | None


class AttemptView(BaseModel):
    attempt: AttemptOut
    lesson: LessonOut
    audio: AudioInfo
    exercises: list[ExerciseOut]


class CurrentAttempt(BaseModel):
    attempt_id: str | None


class StartAttempt(BaseModel):
    model_config = {'extra': 'forbid'}
    client_request_id: str = Field(pattern=REQUEST_ID)
    accent: str | None = Field(default=None, pattern=r'^[a-z_]{2,30}$')


class AnswerBody(BaseModel):
    model_config = {'extra': 'forbid'}
    exercise_id: str = Field(min_length=36, max_length=36)
    option_id: str | None = Field(default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,31}$')   # multiple choice
    text: str | None = Field(default=None, min_length=1, max_length=80)                   # fill in the blank
    order: list[Annotated[str, Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,31}$')]] | None = Field(default=None, min_length=3, max_length=6)  # sequencing


class SignedAudio(BaseModel):
    url: str
    expires_at: datetime
    content_type: str
    duration_ms: int


class SpecIn(BaseModel):
    model_config = {'extra': 'forbid'}
    topic: str = Field(min_length=3, max_length=120)
    cefr: Literal['A1', 'A2', 'B1', 'B2', 'C1', 'C2']
    accent: Literal[ACCENT_IDS]      # type: ignore[valid-type]  # any catalog accent: which ones can actually be generated is decided by gen.spec_problem()
    minutes: int = Field(ge=1, le=10)
    format: Literal['story', 'dialogue', 'conversation']
    scenario: str = Field(default='', max_length=200)
    accent2: Literal[ACCENT_IDS] | None = None   # type: ignore[valid-type]  # the second speaker's accent in a mixed-accent conversation


class InterpretBody(BaseModel):
    model_config = {'extra': 'forbid'}
    client_request_id: str = Field(pattern=REQUEST_ID)
    text: str = Field(min_length=3, max_length=280)


class GenerateBody(BaseModel):
    model_config = {'extra': 'forbid'}
    client_request_id: str = Field(pattern=REQUEST_ID)
    spec: SpecIn
    text: str | None = Field(default=None, max_length=280)


class GenerationView(BaseModel):
    id: str
    kind: str
    phase: str                           # interpreted | needs_input | queued | script | audio | transcript_sync | exercises | ready | failed | rejected | unavailable
    spec: dict | None = None
    notes: list[str] = []
    needs_choice: dict | None = None     # {reason, options:[{label, patch}]}: the requested accent or format cannot be generated as asked; the learner picks before anything is generated
    question: str | None = None
    title: str | None = None
    lesson_id: str | None = None
    failure: str | None = None
    unavailable_reason: str | None = None
    updated_at: datetime | None = None


class Availability(BaseModel):
    available: bool
    reason: str | None = None            # disabled | paused | daily_limit | budget | credits
    max_minutes: int
    accents: list[str]
    accent_labels: dict[str, str] = {}      # id -> learner-facing label, from the shared catalog
    coming_accents: list[str] = []         # accents Audli can name but cannot generate in two voices yet: shown, never selectable
    formats: dict[str, list[str]] = {}     # listening format -> accents that can be generated in it right now (monologue | conversation | mixed_accent_conversation)
    formats_enabled: bool = False          # the flexible-formats flag (mixed-accent conversations and accents beyond the original three)
    per_learner_daily: int


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(404, 'Not found.') from None


def _guard(call):
    try:
        return call()
    except store.NotFound as error:
        raise HTTPException(404, str(error)) from None
    except store.Conflict as error:
        raise HTTPException(409, str(error)) from None
    except store.Invalid as error:
        raise HTTPException(422, str(error)) from None


def _retry_once(write):
    """A concurrent identical request can win a unique constraint; the retry then finds its row and takes the replay path."""
    try:
        return write()
    except IntegrityError:
        return write()


def _view(db, user_id, attempt_id) -> AttemptView:
    data = store.attempt_view(db, user_id, attempt_id)
    audio = data['audio']
    return AttemptView(attempt=data['attempt'], lesson=data['lesson'], exercises=data['exercises'],
                       audio=AudioInfo(id=audio['id'], accent=audio['accent'], duration_ms=audio['duration_ms'], cues=audio['alignment']))


def prepare(engine, worker: bool = False, generation: bool = False, generator: bool = False) -> None:
    """Local/test SQLite creates its tables; PostgreSQL must already have migrations 009-011 (fail closed with a fixed message).
    worker=True checks only what the spoken-answer worker needs (app.listening.roles), so it can run under its own least-privilege role.
    The Milestone 05 generation tables (migration 012) are required only when generation is switched on, so deploying this code never depends on 012."""
    from sqlalchemy import inspect, text
    from app.listening import roles, schema
    if engine.dialect.name == 'sqlite':
        schema.ensure_sqlite(engine)
        return
    with engine.connect() as db:
        present = set(inspect(db).get_table_names(schema='public'))
    generation = generation or generator
    required = tuple(n for n in schema.NAMES if generation or n not in schema.NAMES_012)
    if not set(required) <= present:
        raise RuntimeError('Audli listening lessons need migrations 009, 010 and 011' + (' (and 012 for lesson generation)' if generation else '')
                           + '. Apply docs/postgres-009.sql, docs/postgres-010.sql and docs/postgres-011.sql' + (' and docs/postgres-012.sql' if generation else '') + ' as the backend database owner.')
    needs = ({'SELECT': roles.GENERATOR_SELECT, 'UPDATE': roles.GENERATOR_UPDATE, 'INSERT': roles.GENERATOR_INSERT} if generator else
             {'SELECT': roles.SELECT, 'UPDATE': roles.UPDATE, 'INSERT': roles.INSERT}) if (worker or generator) else {
        'SELECT': required, 'INSERT': ('listening_attempts', 'listening_responses', 'listening_answers', 'listening_spoken', 'listening_jobs', 'listening_ai_limits')
        + (('listening_generation_requests', 'listening_generation_limits') if generation else ()),
        'UPDATE': ('listening_spoken', 'listening_jobs', 'listening_ai_limits') + (('listening_generation_requests', 'listening_generation_limits') if generation else ())}
    pairs = [(table, privilege) for privilege, tables in needs.items() for table in tables]
    with engine.connect() as db:
        lacking = db.execute(text("SELECT count(*) FROM unnest(CAST(:tables AS text[]), CAST(:privileges AS text[])) AS n(name, privilege) "
                                  "WHERE NOT has_table_privilege(current_user, 'public.' || n.name, n.privilege)"),
                             {'tables': [p[0] for p in pairs], 'privileges': [p[1] for p in pairs]}).scalar_one()
    if lacking:
        # e.g. the restricted claims-mode role: the 2.0 tables have no reviewed grants or policies for it yet.
        raise RuntimeError('The database role lacks privileges on the Audli listening tables. Run with the backend owner role, or add reviewed grants first.')


PAUSED = 'Spoken answers are paused for a little while. You can skip this question and carry on; your other answers are saved.'


def register_listening_routes(app, repo, settings, objects, recordings=None, engine=None):
    """`objects` is the lesson-audio store (signed links); `recordings` is the separate, never-served store for temporary spoken answers."""
    recordings = recordings if recordings is not None else objects          # one store only in older tests; main.py always passes both
    allowed = settings.listening_allowed_set
    log = logging.getLogger('audli.listening')
    refused_seen: set[str] = set()
    # Logged at WARNING only because uvicorn's default configuration drops INFO from application loggers. Operators can tell "routes are on, allow-list set" from "off" without any value being printed: counts and 8-character prefixes only.
    log.warning('Audli 2.0 routes registered: storage=%s allow-list=%s entries (%s)', settings.listening_storage, len(allowed),
             ', '.join(sorted(a[:8] for a in allowed)) or 'open to every signed-in learner')

    if settings.listening_storage == 'local' and settings.auth_mode == 'supabase':
        # Signed links would point at this container's own disk, where published lesson audio does not exist: every lesson would show "The audio couldn't load".
        log.warning('Audli 2.0 lesson audio is set to LOCAL storage while accounts are on; set AUDLI_LISTENING_STORAGE=s3 or published audio will not play')

    def require_allowed() -> None:
        """While 2.0 is limited to named test accounts, everyone else sees exactly what they would see if it did not exist."""
        if allowed and repo.learner_id not in allowed:
            prefix = str(repo.learner_id)[:8]
            if prefix not in refused_seen and len(refused_seen) < 50:          # once per distinct account per process; the response is unchanged
                refused_seen.add(prefix)
                log.warning('Audli 2.0 refused a signed-in account not on the allow-list (id starts %s)', prefix)
            raise HTTPException(404, 'Not found.')
    router = APIRouter(prefix='/api/listening', dependencies=[Depends(require_allowed)])

    def require_ai() -> None:
        """No new AI work is accepted while the kill switch is on or today's ceiling cannot cover one more answer. Fails closed."""
        accepting, _ = AiGuard(repo.engine, settings).accepting()      # built per request: in accounts mode `repo` only exists inside one
        if not accepting:
            raise HTTPException(503, PAUSED)

    if settings.listening_ops_token:
        import hmac
        from fastapi import Header

        @app.get('/api/listening/ops/health')
        def ops_health(authorization: Annotated[str | None, Header()] = None):
            """Aggregate numbers for an external monitor. Own bearer token (constant-time comparison); no learner data; absent unless configured."""
            expected = 'Bearer ' + settings.listening_ops_token.get_secret_value()
            if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
                raise HTTPException(401, 'Not authorised.', headers={'WWW-Authenticate': 'Bearer'})
            from app.listening.health import report_with_alerts
            return report_with_alerts(engine if engine is not None else repo.engine, settings)       # the ops route has no learner, so it needs the plain engine


    if settings.listening_generation_enabled:
        from app.listening import generation as gen, generation_jobs as genjobs
        ledger = gen.Ledger(engine if engine is not None else repo.engine, settings)
        plain = engine if engine is not None else repo.engine
        gen_allowed = settings.listening_gen_allowed_set

        def require_generation_allowed() -> None:
            """While generation is limited to named learners, everyone else sees exactly what they would see if it did not exist."""
            if gen_allowed and repo.learner_id not in gen_allowed:
                raise HTTPException(404, 'Not found.')

        def _halted() -> bool:
            return AiGuard(plain, settings).halted()

        @router.get('/generation/availability', response_model=Availability, dependencies=[Depends(require_generation_allowed)])
        def generation_availability():
            ok, reason = ledger.availability(repo.learner_id, 1, 1)
            if ok and _halted():
                ok, reason = False, 'paused'
            return Availability(available=ok, reason=reason or None, max_minutes=settings.listening_gen_max_minutes, accents=list(gen.ACCENTS),
                                accent_labels={a: gen.ACCENT_LABELS[a] for a in gen.ACCENT_IDS},
                                formats=gen.format_availability(settings), formats_enabled=settings.listening_formats_enabled,
                                coming_accents=gen.coming_accents(settings),
                                per_learner_daily=settings.listening_gen_per_learner_daily)

        def _generation_view(request_id: str) -> GenerationView:
            with repo.read() as db:
                data = genjobs.request_view(db, repo.learner_id, request_id)
            if data is None:
                raise HTTPException(404, 'Not found.')                        # also what another learner's request id gets
            if data['phase'] == 'interpreted' and data.get('spec'):
                data['needs_choice'] = gen.needs_choice(data['spec'], data.get('notes') or [], settings)
            return GenerationView(**data)

        @router.post('/generation/interpret', response_model=GenerationView, dependencies=[Depends(require_generation_allowed)])
        def generation_interpret(body: InterpretBody):
            user_id = repo.learner_id
            if _halted():
                raise HTTPException(503, 'Lesson creation is paused for a little while.')
            try:
                with repo.transaction() as db:
                    request_id, _ = genjobs.create_interpretation(db, user_id, body.client_request_id, body.text.strip())
            except gen.GenerationRefused:
                raise HTTPException(429, 'You have made a lot of requests today. Please try again tomorrow.') from None
            return _generation_view(request_id)

        @router.post('/generation/requests', response_model=GenerationView, dependencies=[Depends(require_generation_allowed)])
        def generation_create(body: GenerateBody):
            user_id = repo.learner_id
            problem = gen.spec_problem(body.spec, settings)             # the structured form can only ask for what exists: refuse, never swap an accent behind the learner's back
            if problem:
                raise HTTPException(422, problem)
            spec_raw, notes = gen.clamp_spec(body.spec.model_dump(), settings.listening_gen_max_minutes, settings)
            spec = gen.Spec.model_validate(spec_raw)
            if _halted():
                raise HTTPException(503, 'Lesson creation is paused for a little while.')
            with repo.transaction() as db:
                request_id, created = genjobs.create_generation(db, user_id, body.client_request_id, spec, notes, body.text)
            if created:
                try:
                    genjobs.finalize_reservation(plain, ledger, settings, user_id, request_id, spec)
                except gen.GenerationRefused:
                    pass                                                       # the request row now says phase=unavailable and why; no job was queued
            return _generation_view(request_id)

        @router.get('/generation/requests/{request_id}', response_model=GenerationView, dependencies=[Depends(require_generation_allowed)])
        def generation_status(request_id: str):
            return _generation_view(_uuid(request_id))

    @router.get('/lessons', response_model=list[CatalogItem])
    def catalog():
        with repo.read() as db:
            return store.list_catalog(db, repo.learner_id)

    @router.get('/progress', response_model=ProgressOut)
    def progress():
        with repo.read() as db:
            return store.progress_summary(db, repo.learner_id)

    @router.get('/lessons/{lesson_id}/attempts/current', response_model=CurrentAttempt)
    def current_attempt(lesson_id: str):
        with repo.read() as db:
            return CurrentAttempt(attempt_id=store.current_attempt_id(db, repo.learner_id, _uuid(lesson_id)))

    @router.post('/lessons/{lesson_id}/attempts', response_model=AttemptView)
    def start_attempt(lesson_id: str, body: StartAttempt):
        lesson_id, user_id = _uuid(lesson_id), repo.learner_id

        def write():
            with repo.transaction() as db:
                return _guard(lambda: store.create_attempt(db, user_id, lesson_id, body.client_request_id, body.accent))[0]
        attempt_id = _retry_once(write)
        with repo.read() as db:
            return _view(db, user_id, attempt_id)

    @router.get('/attempts/{attempt_id}', response_model=AttemptView)
    def get_attempt(attempt_id: str):
        with repo.read() as db:
            return _guard(lambda: _view(db, repo.learner_id, _uuid(attempt_id)))

    @router.post('/attempts/{attempt_id}/answers', response_model=AttemptView)
    def answer(attempt_id: str, body: AnswerBody):
        attempt_id, user_id = _uuid(attempt_id), repo.learner_id
        exercise_id = _uuid(body.exercise_id)

        def write():
            with repo.transaction() as db:
                _guard(lambda: store.submit_answer(db, user_id, attempt_id, exercise_id, option_id=body.option_id, text=body.text, order=body.order))
        _retry_once(write)
        with repo.read() as db:
            return _guard(lambda: _view(db, user_id, attempt_id))

    @router.post('/attempts/{attempt_id}/exercises/{exercise_id}/skip', response_model=AttemptView)
    def skip_spoken(attempt_id: str, exercise_id: str):
        attempt_id, exercise_id, user_id = _uuid(attempt_id), _uuid(exercise_id), repo.learner_id

        def write():
            with repo.transaction() as db:
                _guard(lambda: spoken.skip(db, user_id, attempt_id, exercise_id))
        _retry_once(write)
        with repo.read() as db:
            return _view(db, user_id, attempt_id)

    @router.post('/attempts/{attempt_id}/exercises/{exercise_id}/retry', response_model=AttemptView)
    def retry_spoken(attempt_id: str, exercise_id: str):
        attempt_id, exercise_id, user_id = _uuid(attempt_id), _uuid(exercise_id), repo.learner_id

        require_ai()

        def write():
            with repo.transaction() as db:
                _guard(lambda: spoken.retry_assessment(db, user_id, attempt_id, exercise_id))
        _retry_once(write)
        with repo.read() as db:
            return _view(db, user_id, attempt_id)

    @router.post('/attempts/{attempt_id}/exercises/{exercise_id}/recording', response_model=AttemptView)
    async def upload_recording(attempt_id: str, exercise_id: str, request: Request, upload_id: Annotated[str, Query(pattern=REQUEST_ID)]):
        attempt_id, exercise_id, user_id = _uuid(attempt_id), _uuid(exercise_id), repo.learner_id
        data, declared = await request.body(), request.headers.get('content-type')

        def work():
            with repo.read() as db:
                _guard(lambda: spoken.check_target(db, user_id, attempt_id, exercise_id))     # nobody else's ids reach the decoder
            require_ai()
            try:
                recording = validate_recording(data, declared)
            except RecordingRejected as error:
                raise HTTPException(error.status, str(error)) from None

            def claim():
                with repo.transaction() as db:
                    return _guard(lambda: spoken.claim_upload(db, user_id, attempt_id, exercise_id, upload_id, recording.kind,
                                                              recording.content_type, settings.listening_spoken_daily_limit))
            try:
                action, key, _ = _retry_once(claim)
            except spoken.Limited:
                raise HTTPException(429, 'You have reached today\u2019s limit for spoken answers. Skip this question or try again tomorrow.') from None
            if action == 'store':
                try:
                    recordings.put(key, data, recording.content_type)
                except Exception:
                    # The claim stays 'receiving' so the same upload id can resume; the worker's sweep expires it if the learner leaves.
                    raise HTTPException(503, 'We couldn\u2019t save your recording. Please try again.') from None

                def finish():
                    with repo.transaction() as db:
                        _guard(lambda: spoken.finish_upload(db, user_id, attempt_id, exercise_id, upload_id))
                _retry_once(finish)
            with repo.read() as db:
                return _view(db, user_id, attempt_id)
        return await run_in_threadpool(work)

    @router.get('/attempts/{attempt_id}/audio', response_model=SignedAudio)
    def audio(attempt_id: str):
        with repo.read() as db:
            found = _guard(lambda: store.attempt_audio_object(db, repo.learner_id, _uuid(attempt_id)))
        url = objects.signed_url(found['object_key'], SIGNED_URL_SECONDS)
        return SignedAudio(url=url, expires_at=datetime.fromtimestamp(time.time() + SIGNED_URL_SECONDS, timezone.utc),
                           content_type=found['content_type'], duration_ms=found['duration_ms'])

    if isinstance(objects, LocalObjectStore):
        # Reachable without a bearer token on purpose: an <audio> element cannot send one and the signature is the credential.
        # LearnerAuthentication allows exactly this prefix (app/auth.py PUBLIC_PREFIXES).
        @app.get('/api/listening/media/{key:path}')
        def media(key: str, exp: Annotated[int, Query()], sig: Annotated[str, Query(max_length=128)]):
            path = objects.verify(key, exp, sig)
            if path is None:
                raise HTTPException(404, 'Audio not found.')
            return FileResponse(path, media_type='audio/mpeg' if path.suffix == '.mp3' else 'audio/wav',
                                headers={'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline'})

    app.include_router(router)
