"""Learner API for 2.0 listening lessons. No AI provider, no conversation state and no process-wide lock: concurrency control is
the database's (unique constraints, a row lock on the attempt, idempotent writes)."""
import time
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.listening import spoken, store
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


def prepare(engine) -> None:
    """Local/test SQLite creates its tables; PostgreSQL must already have migration 009 (fail closed with a fixed message)."""
    from sqlalchemy import inspect
    from app.listening import schema
    if engine.dialect.name == 'sqlite':
        schema.ensure_sqlite(engine)
        return
    with engine.connect() as db:
        present = set(inspect(db).get_table_names(schema='public'))
    if not set(schema.NAMES) <= present:
        raise RuntimeError('Audli listening lessons need migrations 009 and 010. Apply docs/postgres-009.sql and docs/postgres-010.sql as the backend database owner.')
    with engine.connect() as db:
        from sqlalchemy import text
        lacking = db.execute(text(
            "SELECT count(*) FROM unnest(CAST(:names AS text[])) AS n(name) WHERE NOT (has_table_privilege(current_user, 'public.' || n.name, 'SELECT') "
            "AND (n.name NOT IN ('listening_attempts', 'listening_responses', 'listening_answers', 'listening_spoken', 'listening_jobs') "
            "OR has_table_privilege(current_user, 'public.' || n.name, 'INSERT')) "
            "AND (n.name NOT IN ('listening_spoken', 'listening_jobs') OR has_table_privilege(current_user, 'public.' || n.name, 'UPDATE')))"),
            {'names': list(schema.NAMES)}).scalar_one()
    if lacking:
        # e.g. the restricted claims-mode role: the 2.0 tables have no reviewed grants or policies for it yet.
        raise RuntimeError('The database role lacks privileges on the Audli listening tables. Run with the backend owner role, or add reviewed grants first.')


def register_listening_routes(app, repo, settings, objects):
    @app.get('/api/listening/lessons', response_model=list[CatalogItem])
    def catalog():
        with repo.read() as db:
            return store.list_catalog(db, repo.learner_id)

    @app.get('/api/listening/progress', response_model=ProgressOut)
    def progress():
        with repo.read() as db:
            return store.progress_summary(db, repo.learner_id)

    @app.get('/api/listening/lessons/{lesson_id}/attempts/current', response_model=CurrentAttempt)
    def current_attempt(lesson_id: str):
        with repo.read() as db:
            return CurrentAttempt(attempt_id=store.current_attempt_id(db, repo.learner_id, _uuid(lesson_id)))

    @app.post('/api/listening/lessons/{lesson_id}/attempts', response_model=AttemptView)
    def start_attempt(lesson_id: str, body: StartAttempt):
        lesson_id, user_id = _uuid(lesson_id), repo.learner_id

        def write():
            with repo.transaction() as db:
                return _guard(lambda: store.create_attempt(db, user_id, lesson_id, body.client_request_id, body.accent))[0]
        attempt_id = _retry_once(write)
        with repo.read() as db:
            return _view(db, user_id, attempt_id)

    @app.get('/api/listening/attempts/{attempt_id}', response_model=AttemptView)
    def get_attempt(attempt_id: str):
        with repo.read() as db:
            return _guard(lambda: _view(db, repo.learner_id, _uuid(attempt_id)))

    @app.post('/api/listening/attempts/{attempt_id}/answers', response_model=AttemptView)
    def answer(attempt_id: str, body: AnswerBody):
        attempt_id, user_id = _uuid(attempt_id), repo.learner_id
        exercise_id = _uuid(body.exercise_id)

        def write():
            with repo.transaction() as db:
                _guard(lambda: store.submit_answer(db, user_id, attempt_id, exercise_id, option_id=body.option_id, text=body.text, order=body.order))
        _retry_once(write)
        with repo.read() as db:
            return _guard(lambda: _view(db, user_id, attempt_id))

    @app.post('/api/listening/attempts/{attempt_id}/exercises/{exercise_id}/skip', response_model=AttemptView)
    def skip_spoken(attempt_id: str, exercise_id: str):
        attempt_id, exercise_id, user_id = _uuid(attempt_id), _uuid(exercise_id), repo.learner_id

        def write():
            with repo.transaction() as db:
                _guard(lambda: spoken.skip(db, user_id, attempt_id, exercise_id))
        _retry_once(write)
        with repo.read() as db:
            return _view(db, user_id, attempt_id)

    @app.post('/api/listening/attempts/{attempt_id}/exercises/{exercise_id}/retry', response_model=AttemptView)
    def retry_spoken(attempt_id: str, exercise_id: str):
        attempt_id, exercise_id, user_id = _uuid(attempt_id), _uuid(exercise_id), repo.learner_id

        def write():
            with repo.transaction() as db:
                _guard(lambda: spoken.retry_assessment(db, user_id, attempt_id, exercise_id))
        _retry_once(write)
        with repo.read() as db:
            return _view(db, user_id, attempt_id)

    @app.post('/api/listening/attempts/{attempt_id}/exercises/{exercise_id}/recording', response_model=AttemptView)
    async def upload_recording(attempt_id: str, exercise_id: str, request: Request, upload_id: Annotated[str, Query(pattern=REQUEST_ID)]):
        attempt_id, exercise_id, user_id = _uuid(attempt_id), _uuid(exercise_id), repo.learner_id
        data, declared = await request.body(), request.headers.get('content-type')

        def work():
            with repo.read() as db:
                _guard(lambda: spoken.check_target(db, user_id, attempt_id, exercise_id))     # nobody else's ids reach the decoder
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
                    objects.put(key, data, recording.content_type)
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

    @app.get('/api/listening/attempts/{attempt_id}/audio', response_model=SignedAudio)
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
