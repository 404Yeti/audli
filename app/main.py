import asyncio
import json
from tempfile import NamedTemporaryFile
from pathlib import Path
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from app.adaptive import adapt
from app.audio import validate_upload, sanitize_generated_audio
from app.config import Settings
from app.diagnostics import operation, report_failure
from app.evaluation import score_judgments
from app.models import ExerciseContent, StrictModel, Transcription
from app.repository import create_repository, SQLProgressRepository
from app.services.provider import AIProvider
from app.conversation_api import register_conversation_routes
from app.onboarding_api import register_onboarding_routes
from app.onboarding import application_destination
from app.security import LocalRequestGuard
from app.auth import RequestRepository, SupabaseIdentity, LearnerAuthentication

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
    identity = SupabaseIdentity(settings, auth_client) if settings.auth_mode == 'supabase' else None
    repo = RequestRepository(database if identity is None else None)
    audio_dir = settings.data_dir / 'exercise_audio'
    audio_dir.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            database.close()
            if identity is not None:
                await identity.close()
            if hasattr(provider, 'client'):
                await provider.client.close()

    app = FastAPI(title='Audli', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if identity is not None:
        app.add_middleware(LearnerAuthentication, identity=identity, repository=database, request_repository=repo)
    app.add_middleware(LocalRequestGuard, max_audio_bytes=settings.max_audio_bytes, allowed_origins=settings.browser_origins)
    app.state.repository = database

    def ai():
        nonlocal provider
        if provider is None:
            if settings.provider == 'demo':
                from app.services.demo import DemoProvider
                provider = DemoProvider()
            else:
                from app.services.openai_provider import OpenAIProvider
                provider = OpenAIProvider(settings)
        return provider

    @app.middleware('http')
    async def privacy_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            report_failure(settings, f'{request.method} {request.url.path}', exc)
            response = JSONResponse(status_code=503, content={'detail': 'Audli could not finish that step. Check server configuration and try again; your progress is safe.'})
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

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
        # Strict allowlist. Even title/topic/questions can reveal answers.
        return {'id': row['id'], 'difficulty': json.loads(row['difficulty']),
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
        return {'status': 'ok', 'provider': settings.provider}

    @app.get('/api/auth/config')
    async def auth_config():
        return {'mode': settings.auth_mode}

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

    @app.get('/api/exercises/current')
    async def current():
        row = repo.current_exercise()
        return public_exercise(row) if row else None

    @app.post('/api/exercises')
    async def generate():
        async with lock:
            current = repo.current_exercise()
            if current and not repo.completed(current['id']):
                return public_exercise(current)
            profile = repo.profile()
            with operation(settings, 'ExerciseGenerator.generate'):
                content = await ai().generate(profile)
            d = profile.difficulty
            if (content.speech_rate, content.target_duration_seconds, content.vocabulary_level, content.information_density) != (d.speech_rate, d.duration_seconds, d.vocabulary_level, d.information_density):
                raise ValueError('Provider difficulty mismatch')
            with operation(settings, 'SpeechService.speech'):
                audio = await ai().speech(content)
            if not audio:
                raise ValueError('Empty generated audio')
            exercise_id = str(uuid4())
            kind = 'wav' if settings.provider == 'demo' else 'mp3'
            with operation(settings, 'SpeechService.sanitize_generated_audio'):
                audio = await sanitize_generated_audio(audio, kind)
            audio_name = exercise_id + '.' + kind
            path = audio_dir / audio_name
            path.write_bytes(audio)
            try:
                repo.save_exercise(exercise_id, content, profile.difficulty, audio_name, audio=audio)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return public_exercise(repo.exercise(exercise_id))

    @app.get('/api/exercises/{exercise_id}/audio')
    async def audio(exercise_id: str):
        row = exercise_or_404(exercise_id)
        path = restore_audio(row['audio_name'])
        if not path.exists():
            raise HTTPException(404, 'Audio file missing. Restore the audio backup or retry.')
        return FileResponse(path, media_type='audio/wav' if path.suffix == '.wav' else 'audio/mpeg', filename='audli-listening'+path.suffix)

    @app.post('/api/exercises/{exercise_id}/attempts')
    async def transcribe(exercise_id: str, audio: UploadFile):
        async with lock:
            exercise_or_404(exercise_id)
            current = repo.current_exercise()
            if current['id'] != exercise_id or repo.completed(exercise_id):
                raise HTTPException(409, 'This exercise is already complete.')
            try:
                data, filename = await validate_upload(audio, settings)
                transcription = await ai().transcribe(data, filename)
            finally:
                await audio.close()
            attempt_id = repo.add_attempt(exercise_id, transcription)
            conversation = repo.conversation(exercise_id)
            if conversation:
                conversation.pending_attempt_id, conversation.pending_text = attempt_id, None
                conversation.state = 'AWAITING_FOLLOWUP' if conversation.active_followup else 'AWAITING_SUMMARY'
                repo.save_conversation(conversation)
            return {'id': attempt_id, 'transcription': transcription}

    @app.post('/api/attempts/{attempt_id}/evaluate')
    async def evaluate(attempt_id: str, body: Confirmation):
        async with lock:
            attempt = repo.attempt(attempt_id)
            if not attempt:
                raise HTTPException(404, 'Attempt not found.')
            previous = repo.result(attempt_id)
            if previous:
                return previous
            if repo.conversation(attempt['exercise_id']):
                raise HTTPException(409, 'Finish the current spoken assessment, including its follow-ups.')
            if not body.confirmed:
                raise HTTPException(422, 'Confirm what you said before evaluating.')
            if len(body.text.strip().split()) < 3:
                raise HTTPException(422, 'Recognition needs at least a few words. Please record again.')
            row = exercise_or_404(attempt['exercise_id'])
            if repo.completed(row['id']) or repo.current_exercise()['id'] != row['id']:
                raise HTTPException(409, 'This exercise is already complete.')
            content = ExerciseContent.model_validate_json(row['content'])
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

    register_onboarding_routes(app, repo, ai, lock, settings)
    register_conversation_routes(app, repo, ai, lock, settings, audio_dir, exercise_or_404, restore_audio)
    return app

app = create_app()
