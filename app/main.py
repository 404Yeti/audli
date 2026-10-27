import os
import asyncio
import json
import logging
from tempfile import NamedTemporaryFile
from pathlib import Path
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import Field
from app.adaptive import adapt
from app.audio import validate_upload, sanitize_generated_audio
from app.config import Settings
from app.diagnostics import operation, report_failure, timed_lock, request_timings
from app.telemetry import aggregator, loop_lag_loop, summary_loop
from app.upload_idempotency import attempt_id_for, upload_id_from
from app.upload_policy import register_upload_policy_routes, resolve_policy
from app.telemetry_api import register_telemetry_routes
from time import perf_counter
from app.evaluation import score_judgments
from app.models import ExerciseContent, StrictModel, Transcription, AccentPreferences, AccentPreferenceUpdate
from app.repository import create_repository, SQLProgressRepository
from app.services.provider import AIProvider
from app.conversation_api import register_conversation_routes
from app.onboarding_api import register_onboarding_routes
from app.onboarding_reset import register_onboarding_reset_routes
from app.lesson_api import register_lesson_routes
from app.review_api import register_review_routes
from app.onboarding import application_destination
from app.security import LocalRequestGuard
from app.uploadtrace import UploadTrace
from app.limits import LessonLimits
from app.lesson_limit import LimitRefused
from app.auth import RequestRepository, SupabaseIdentity, LearnerAuthentication
from app import turntrace
from app.offload import Offload
from app.dbtiming import instrument_engine, request_db, new_request_accumulator, finish_request

class Onboarding(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=500)

class Confirmation(StrictModel):
    text: str = Field(min_length=3, max_length=8000)
    confirmed: bool


def create_app(settings: Settings | None = None, provider: AIProvider | None = None,
               repository: SQLProgressRepository | None = None, auth_client=None):
    settings = settings or Settings()
    database = repository or create_repository(settings)
    if settings.latency_aggregates and not getattr(database.engine, '_audli_timed', False):
        instrument_engine(database.engine, 'db')  # AUD-82: numeric-only pool/query timing; no behaviour change.
        database.engine._audli_timed = True
    if settings.lesson_limit_mode != 'off':
        database.require_lesson_limit_schema()
    if settings.db_lazy_ping and database.engine.dialect.name == 'postgresql':
        from app.lazyping import install_lazy_ping
        install_lazy_ping(database.engine, settings.db_lazy_ping_seconds)    # after instrumentation, so pool_ping counts only real pings
    database.limit_mode = settings.lesson_limit_mode
    database.require_auth_user = settings.account_deletion_enabled
    read_autocommit = {'requested': settings.db_read_autocommit, 'effective': False, 'reason': 'flag off'}
    if settings.db_read_autocommit:
        # Only on a server whose default isolation is READ COMMITTED, where a read transaction pins no snapshot anyway.
        allowed, reason = database.autocommit_reads_allowed()
        database.read_autocommit = allowed
        read_autocommit.update(effective=allowed, reason=reason)
        # /api/health shows only requested/effective; the reason (isolation level) stays in this internal log line.
        logging.getLogger('audli').warning('AUDLI_DB_READ_AUTOCOMMIT %s (%s)', 'enabled' if allowed else 'IGNORED', reason)
    if settings.review_improvement_enabled:
        database.require_review_improvement_schema()
    identity = SupabaseIdentity(settings, auth_client) if settings.auth_mode == 'supabase' else None
    offload = Offload(settings.db_offload)
    repo = RequestRepository(database if identity is None else None)
    audio_dir = settings.data_dir / 'exercise_audio'
    audio_dir.mkdir(parents=True, exist_ok=True)
    from app.locking import LearnerLocks
    def new_lock():
        # Key comes only from the server-verified request scope (RequestRepository), never from the client.
        return LearnerLocks(settings.per_learner_locks, lambda: repo.learner_id)
    lock = new_lock()
    from app.storage.accent_controls import SharedAccentControls
    passage_limits = SharedAccentControls(database.engine, settings)

    @asynccontextmanager
    async def lifespan(app):
        summary = lag = None
        try:
            from app.services.tutor_tts import emit_configuration
            emit_configuration(settings)
            from app.default_passage import emit_default_passage_configuration
            emit_default_passage_configuration(settings)
            summary = asyncio.create_task(summary_loop(settings.latency_summary_seconds)) if settings.latency_aggregates else None
            lag = asyncio.create_task(loop_lag_loop()) if settings.latency_aggregates else None
            yield
        finally:
            for task in (summary, lag):
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            passage_limits.close()
            database.close()
            if identity is not None:
                await identity.close()
            from app.services.tutor_tts import TutorTTSProvider
            if isinstance(provider, TutorTTSProvider):
                await provider.close()
            elif hasattr(provider, 'client'):
                await provider.client.close()

    app = FastAPI(title='Audli', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if identity is not None:
        app.add_middleware(LearnerAuthentication, identity=identity, repository=database, request_repository=repo,
            timing=settings.latency_aggregates, ack_exempt=settings.ack_lease_exempt, lease_pool=settings.lease_pool, pool_size=settings.lease_pool_size, pool_overflow=settings.lease_pool_overflow, offload=offload)
    app.add_middleware(LocalRequestGuard, max_audio_bytes=settings.max_audio_bytes, allowed_origins=settings.browser_origins)
    app.state.accent_controls = passage_limits
    app.state.repository = database
    app.state.limits = LessonLimits(repo, settings)
    app.state.locks = lock
    app.state.offload = offload

    def ai():
        nonlocal provider
        if provider is None:
            if settings.provider == 'demo':
                from app.services.demo import DemoProvider
                provider = DemoProvider()
            else:
                from app.services.openai_provider import OpenAIProvider
                from app.services.tutor_tts import TutorTTSProvider
                provider = TutorTTSProvider(OpenAIProvider(settings), settings)
        if settings.tutor_tts_provider == 'elevenlabs':
            from app.services.tutor_tts import TutorTTSProvider
            if not isinstance(provider, TutorTTSProvider):
                provider = TutorTTSProvider(provider, settings)
        return provider

    turntrace.enabled = bool(settings.turn_trace and settings.latency_aggregates)
    upload_policy = resolve_policy(settings.upload_retry and settings.upload_idempotency, settings.upload_retry_policy)   # AUD-89 P2: computed once; invalid = disabled

    @app.middleware('http')
    async def privacy_headers(request: Request, call_next):
        timings = [] if settings.latency_telemetry or settings.environment == 'development' else None
        token = request_timings.set(timings)
        db_token = request_db.set(new_request_accumulator()) if settings.latency_aggregates else None
        trace = turntrace.begin(request.scope['headers'])
        trace_token = turntrace.request_trace.set(trace) if trace is not None else None
        response = None
        started = perf_counter()
        try:
            with operation(settings, 'request_total'):
                response = await call_next(request)
        except Exception as exc:
            report_failure(settings, f'{request.method} {request.url.path}', exc)
            response = JSONResponse(status_code=503, content={'detail': 'Audli could not finish that step. Check server configuration and try again; your progress is safe.'})
        finally:
            if db_token is not None:
                if trace is not None:
                    turntrace.finish(trace, request.method, getattr(request.scope.get('route'), 'path', None),
                                     getattr(response, 'status_code', 0), (perf_counter() - started) * 1000, request_db.get())
                finish_request(request.method, (perf_counter() - started) * 1000,
                               getattr(request.scope.get('route'), 'path', None))
                request_db.reset(db_token)
            if trace_token is not None:
                turntrace.request_trace.reset(trace_token)
            request_timings.reset(token)
            if settings.latency_aggregates:
                # Route template only (e.g. /api/lessons/{lesson_id}/heard), never the concrete path.
                route = getattr(request.scope.get('route'), 'path', None)
                aggregator.record(f'http.{request.method} {route}' if route else 'http.unmatched', (perf_counter() - started) * 1000)
        if timings:
            response.headers['Server-Timing'] = ', '.join(
                f'{name.replace(".", "_")};dur={duration:.1f}' for name, duration in timings)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    # AUD-89: outermost, so it sees the upload exactly as the proxy delivered it (before the guard reads the body).
    app.add_middleware(UploadTrace)

    @app.exception_handler(LimitRefused)
    async def lesson_limit_refused(request, exc):
        retry = exc.retry_after()
        return JSONResponse(status_code=exc.status_code, content=exc.body(),
                            headers={'Retry-After': str(retry)} if retry else None)

    @app.exception_handler(Exception)
    async def provider_error(request, exc):
        # Only type is logged: provider errors may contain scripts, text or request payloads.
        report_failure(settings, f'{request.method} {request.url.path}', exc)
        return JSONResponse(status_code=503, content={'detail': 'Audli could not finish that step. Check server configuration and try again; your progress is safe.'})

    def exercise_or_404(exercise_id):
        row = repo.exercise(exercise_id)
        if not row:
            raise HTTPException(404, 'Exercise not found.')
        return row

    def public_exercise(row):
        from app.lesson import public_topic
        # Scenario label only; scripts, titles, questions and answers stay gated.
        return {'id': row['id'], 'difficulty': json.loads(row['difficulty']),
                'topic': public_topic(json.loads(row['content'])),
                'passage_voice': json.loads(row['content']).get('passage_voice'),
                'audio_url': f"/api/exercises/{row['id']}/audio", 'completed_attempt_id': repo.completed(row['id'])}

    def restore_audio(name):
        if not repo.owns_audio(name):
            raise HTTPException(404, 'Audio not found.')
        path = audio_dir / name
        if not path.exists():
            data = repo.audio_blob(name)
            if data is not None:
                temporary = None
                try:
                    with NamedTemporaryFile(dir=audio_dir, delete=False) as output:
                        temporary = output.name
                        output.write(data)
                    Path(temporary).replace(path)
                finally:
                    if temporary is not None:
                        Path(temporary).unlink(missing_ok=True)
        return path

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'provider': settings.provider,
                'revision': os.getenv('RENDER_GIT_COMMIT'),
                'expanded_accent_library_enabled': settings.expanded_accent_library_enabled,
                'available_passage_accents': sorted(settings.available_passage_voices),
                'passage_voice_ids': {accent: voice.voice_id for accent, voice in settings.available_passage_voices.items()},
                'tutor_tts_provider': settings.tutor_tts_provider,
                'tutor_voice_id': settings.elevenlabs_voice_id,
                'passage_tts_model': settings.passage_tts_model,
                'passage_tts_daily_characters': settings.passage_tts_daily_characters,
                'passage_tts_max_concurrent': settings.passage_tts_max_concurrent,
                'db_read_autocommit': {'requested': read_autocommit['requested'], 'effective': read_autocommit['effective']},
                'db_state_only_transitions': settings.db_state_only_transitions,
                'db_offload': settings.db_offload, 'db_lazy_ping': settings.db_lazy_ping,
                'db_state_only_checkpoints': settings.db_state_only_checkpoints,
                'upload_idempotency': settings.upload_idempotency, 'upload_retry': upload_policy['enabled']}

    @app.get('/api/auth/config')
    async def auth_config():
        return {'mode': settings.auth_mode, 'homework_enabled': settings.homework_enabled}

    @app.get('/api/profile')
    async def profile():
        profile = repo.profile()
        return {'profile': profile, 'provider': settings.provider,
                'recognition_min_confidence': settings.min_transcription_confidence,
                'destination': application_destination(profile)}

    @app.put('/api/profile')
    async def onboard(body: Onboarding):
        async with lock:
            profile = repo.profile()
            if (repo.current_exercise() or profile.onboarding.revision > 0
                    or profile.onboarding_status in ('in_progress', 'complete')):
                raise HTTPException(409, 'Your profile is already set up.')
            profile.name, profile.goal = body.name.strip(), body.goal.strip()
            if not profile.name or not profile.goal:
                raise HTTPException(422, 'Enter your name and listening goal.')
            profile.onboarding_status = 'profile_saved'
            repo.save_profile(profile)
            return profile

    @app.put('/api/profile/accent-preferences')
    async def update_accent_preferences(body: AccentPreferenceUpdate):
        from app.accents import LABELS
        accents = list(dict.fromkeys(body.accents))
        preferences = AccentPreferences(accents=accents,
            status='preferred' if accents else 'no_preference',
            response=', '.join(LABELS[accent] for accent in accents) if accents else 'No particular accent difficulty')
        async with lock:
            return repo.save_accent_preferences(preferences)

    @app.get('/api/exercises/current')
    async def current():
        row = repo.current_exercise()
        return public_exercise(row) if row else None

    async def prepare_exercise(lesson=None):
        profile = await offload.run(repo.profile)
        if profile.onboarding.revision > 0 and profile.onboarding_status != 'complete':
            raise HTTPException(409, 'Complete your spoken onboarding first.')
        current = await offload.run(repo.current_exercise)
        if current and not await offload.run(repo.completed, current['id']):
            if lesson is not None:
                await offload.run(repo.attach_lesson_exercise, lesson, current['id'])
            return await offload.run(public_exercise, current)
        profile = await offload.run(repo.profile)
        if lesson and lesson.plan_topic:
            profile = profile.model_copy(deep=True)
            profile.interests = [lesson.plan_topic]
            profile.focus = lesson.focus
        with operation(settings, 'ExerciseGenerator.generate'):
            content = await ai().generate(profile)
        d = profile.difficulty
        if (content.speech_rate, content.target_duration_seconds, content.vocabulary_level, content.information_density) != (d.speech_rate, d.duration_seconds, d.vocabulary_level, d.information_density):
            raise ValueError('Provider difficulty mismatch')
        with operation(settings, 'SpeechService.speech'):
            from app.accent_practice import passage_audio, authorized_accent
            content.passage_voice = None
            audio, routing = await passage_audio(settings, ai(), content, authorized_accent(lesson) if lesson else None, passage_limits, owner=repo.learner_id, lesson=lesson)
            if routing.accent != 'default' and (not settings.accent_practice_enabled or lesson is None or authorized_accent(lesson) != routing.accent):
                audio, routing = await passage_audio(settings, ai(), content, None, passage_limits)
        if not audio:
            raise ValueError('Empty generated audio')
        exercise_id = str(uuid4())
        kind = 'wav' if settings.provider == 'demo' else 'mp3'
        with operation(settings, 'SpeechService.sanitize_generated_audio'):
            audio = await sanitize_generated_audio(audio, kind)
        content.passage_voice = routing
        logging.getLogger('uvicorn.error').info('Passage routing provider=%s requested=%s fallback=%s consent=%s',
            routing.provider, bool(lesson and authorized_accent(lesson)), routing.fallback,
            lesson.accent_consent.state if lesson else 'none')
        if (lesson and routing.accent == 'default'
                and lesson.accent_consent.state in ('accepted', 'expired')
                and lesson.accent_consent.exercise_number == lesson.exercises_completed):
            # The requested voice could not be committed (including expiry or
            # a kill-switch race). The introduction must describe this audio.
            content.passage_voice.fallback = True
        audio_name = exercise_id + '.' + kind
        def persist():
            # Keep file cleanup, the complete existing transaction and projection
            # in one worker section. Cancellation waits for its commit/rollback.
            path = audio_dir / audio_name
            try:
                path.write_bytes(audio)
                repo.save_exercise(exercise_id, content, profile.difficulty, audio_name, audio=audio, lesson=lesson, onboarding_revision=profile.onboarding.revision)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return public_exercise(repo.exercise(exercise_id))
        return await offload.run(persist)


    @app.post('/api/exercises')
    async def generate():
        async with lock:
            lesson = await offload.run(repo.lesson)
            if lesson:
                if lesson.status == 'active' and lesson.phase == 'EXERCISE' and lesson.exercise_id:
                    return await offload.run(lambda: public_exercise(exercise_or_404(lesson.exercise_id)))
                raise HTTPException(409, 'Continue the structured lesson before preparing an exercise.')
            await offload.run(app.state.limits.admit_legacy, 'exercises.generate')  # Lesson-free generation is a billable bypass.
            return await prepare_exercise()

    @app.get('/api/exercises/{exercise_id}/audio')
    async def audio(exercise_id: str):
        row = exercise_or_404(exercise_id)
        path = restore_audio(row['audio_name'])
        if not path.exists():
            raise HTTPException(404, 'Audio file missing. Restore the audio backup or retry.')
        return FileResponse(path, media_type='audio/wav' if path.suffix == '.wav' else 'audio/mpeg', filename='audli-listening'+path.suffix)

    @app.post('/api/exercises/{exercise_id}/attempts')
    async def transcribe(exercise_id: str, audio: UploadFile, request: Request, response: Response):
        # AUD-89 P1 (flag): a recording sent again under the same upload id returns its first attempt. Without a valid header,
        # or with the flag off, attempt_key is None and every line below behaves exactly as it always has.
        upload = upload_id_from(request.headers) if settings.upload_idempotency else None
        attempt_key = attempt_id_for(repo.learner_id, exercise_id, upload) if upload else None

        def mark_pending(conversation, attempt_id):
            conversation.pending_attempt_id, conversation.pending_text = attempt_id, None
            conversation.state = 'AWAITING_FOLLOWUP' if conversation.active_followup else 'AWAITING_SUMMARY'
            if settings.db_state_only_checkpoints:
                repo.save_conversation_checkpoint(conversation)
            else:
                repo.save_conversation(conversation)

        def replay():
            """The first attempt of this upload, or None. Read-only except that a pending marker the first request never got to
            write (it failed between the attempt and the marker) is completed, exactly as the first request would have."""
            row = repo.attempt(attempt_key)
            if not row or row['exercise_id'] != exercise_id:
                return None
            conversation = repo.conversation(exercise_id)
            if (conversation and conversation.pending_attempt_id is None
                    and not any(turn.attempt_id == attempt_key for turn in conversation.turns)):
                mark_pending(conversation, attempt_key)
            return {'id': attempt_key, 'transcription': Transcription.model_validate_json(row['transcription'])}
        async with timed_lock(settings, lock, 'turn.upload'):
            if attempt_key:
                first = await offload.run(replay)
                if first is not None:
                    await audio.close()
                    response.headers['X-Audli-Replayed'] = 'true'
                    aggregator.record('upload.replayed', 0)
                    return first
            def admit():
                lesson = repo.lesson()
                if lesson and (lesson.status != 'active' or lesson.phase != 'EXERCISE' or lesson.exercise_id != exercise_id):
                    return HTTPException(409, 'Resume the active lesson exercise first.')
                try:
                    app.state.limits.admit_exercise('exercise.attempt', exercise_id)
                except LimitRefused as refusal:
                    return refusal
                exercise_or_404(exercise_id)
                current = repo.current_exercise()
                if current['id'] != exercise_id or repo.completed(exercise_id):
                    raise HTTPException(409, 'This exercise is already complete.')
            refusal = await offload.run(admit)
            if refusal is not None:
                await audio.close()
                raise refusal
            try:
                with operation(settings, 'turn.upload_validation'):
                    data, filename = await validate_upload(audio, settings)
                with operation(settings, 'turn.transcription'):
                    try:
                        transcription = await ai().transcribe(data, filename)
                    except Exception:
                        raise HTTPException(503, 'I had trouble transcribing your recording. Please try again.') from None
            finally:
                await audio.close()
            def persist():
                if attempt_key:
                    attempt_id, created = repo.add_attempt_once(exercise_id, transcription, attempt_key)
                else:
                    attempt_id, created = repo.add_attempt(exercise_id, transcription), True
                if not created:
                    # Only reachable when another request of the same upload won the insert between the check above and here
                    # (the lease and the lock make that unlikely; the primary key makes it impossible to duplicate).
                    return replay() or {'id': attempt_id, 'transcription': transcription}
                conversation = repo.conversation(exercise_id)
                if conversation:
                    mark_pending(conversation, attempt_id)
                return {'id': attempt_id, 'transcription': transcription}
            return await offload.run(persist)

    @app.post('/api/attempts/{attempt_id}/evaluate')
    async def evaluate(attempt_id: str, body: Confirmation):
        async with lock:
            attempt = repo.attempt(attempt_id)
            if not attempt:
                raise HTTPException(404, 'Attempt not found.')
            previous = repo.result(attempt_id)
            if previous:
                return previous
            if repo.lesson() or repo.conversation(attempt['exercise_id']):
                raise HTTPException(409, 'Finish the current spoken assessment, including its follow-ups.')
            if not body.confirmed:
                raise HTTPException(422, 'Confirm what you said before evaluating.')
            if len(body.text.strip().split()) < 3:
                raise HTTPException(422, 'Recognition needs at least a few words. Please record again.')
            row = exercise_or_404(attempt['exercise_id'])
            if repo.completed(row['id']) or repo.current_exercise()['id'] != row['id']:
                raise HTTPException(409, 'This exercise is already complete.')
            content = ExerciseContent.model_validate_json(row['content'])
            app.state.limits.admit_exercise('exercise.evaluate', row['id'])
            with operation(settings, 'ComprehensionEvaluator.evaluate'):
                judgments = await ai().evaluate(content, body.text.strip())
            if judgments.transcription_concern:
                return JSONResponse(status_code=422, content={'detail': 'This transcript may be unreliable. Please check it or record again. No score or difficulty change was applied.', 'uncertain': True})
            with operation(settings, 'ComprehensionEvaluator.score_judgments'):
                evaluation = score_judgments(content, judgments, body.text.strip())
            with operation(settings, 'AdaptiveEngine.adapt'):
                profile, event = adapt(repo.profile(), evaluation, settings)
            with operation(settings, 'ProgressRepository.complete'):
                repo.complete(attempt_id, body.text.strip(), evaluation, event, profile)
            return {'evaluation': evaluation, 'adaptation': event}

    @app.get('/api/attempts/{attempt_id}')
    async def attempt_result(attempt_id: str):
        if not repo.attempt(attempt_id):
            raise HTTPException(404, 'Attempt not found.')
        result = repo.result(attempt_id)
        if not result:
            raise HTTPException(409, 'Attempt has not been evaluated.')
        return result

    @app.get('/api/exercises/{exercise_id}/transcript')
    async def transcript(exercise_id: str):
        row = exercise_or_404(exercise_id)
        if not repo.completed(exercise_id):
            raise HTTPException(403, 'Make a spoken attempt and complete evaluation first.')
        content = ExerciseContent.model_validate_json(row['content'])
        return {'title': content.title, 'script': content.script}

    @app.get('/api/history')
    async def history():
        return repo.history()

    from app.tutor_voice_api import register_tutor_voice_routes
    register_tutor_voice_routes(app, repo, lock, settings, new_lock)
    register_telemetry_routes(app, settings)
    register_upload_policy_routes(app, upload_policy)
    from app.homework_api import register_homework_routes
    register_homework_routes(app, repo, settings, database, ai, lock)
    from app.account import register_account_routes
    register_account_routes(app, repo, settings, identity, lock, audio_dir)
    register_review_routes(app, repo, settings)
    from app.improvement_api import register_improvement_routes
    register_improvement_routes(app, repo, ai, settings)
    register_onboarding_routes(app, repo, ai, lock, settings)
    register_onboarding_reset_routes(app, repo, lock)
    register_conversation_routes(app, repo, ai, lock, settings, audio_dir, exercise_or_404, restore_audio, new_lock)
    register_lesson_routes(app, repo, ai, lock, settings, prepare_exercise, public_exercise, passage_limits, new_lock)
    if settings.cors_enabled:
        from starlette.middleware.cors import CORSMiddleware
        # Added last, so outermost: preflights are answered before authentication (they carry no Authorization header)
        # and 401/403/413 responses stay readable by the allowed origin. Exact origins only; credentials are never allowed.
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.browser_origins), allow_credentials=False,
            allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Authorization', 'Content-Type'],
            expose_headers=['Retry-After', 'X-Audli-Tutor-Fallback'], max_age=600)
    return app

app = create_app()
