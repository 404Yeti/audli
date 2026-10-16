"""Optional, authenticated homework. Never invoked by lesson completion writes."""
import asyncio
from datetime import timedelta
from uuid import UUID
from fastapi import HTTPException, UploadFile
from sqlalchemy import inspect
from app.homework import (HomeworkView, Assignment, Decision, StatusChange, HomeworkEngine,
    DiscoveryUnavailable, search_plan, relevance, objective)
from app.services.youtube import YouTubeProvider
from app.audio import validate_upload
from app.models import Transcription
from app.recognition import reliable_recognition
from app.homework_voice import spoken_decision
from app.storage import homework as store
from app.storage import schema as s
from app.storage.repository import utcnow


def register_homework_routes(app, repo, settings, database, ai, lock):
    provider = YouTubeProvider(settings.youtube_api_key.get_secret_value() if settings.youtube_api_key else '')
    engine = HomeworkEngine(provider)
    app.state.homework_engine = engine  # injectable provider for offline contract tests
    if settings.homework_enabled:
        inspector = inspect(database.engine)
        tables = set(inspector.get_table_names())
        if not all(table.name in tables and set(table.columns.keys()).issubset({column['name'] for column in inspector.get_columns(table.name)}) for table in s.HOMEWORK_TABLES):
            raise RuntimeError('Homework requires migration 005. Disable homework or apply the migration before enabling.')

    def enabled():
        if not settings.homework_enabled:
            raise HTTPException(404, 'Optional listening homework is disabled.')

    def owned(action, *args):
        try:
            return action(repo, *args)
        except LookupError:
            raise HTTPException(404, 'Listening assignment not found.') from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.get('/api/homework', response_model=HomeworkView)
    async def listing():
        if not settings.homework_enabled:
            return HomeworkView(enabled=False)
        return HomeworkView(enabled=True, assignments=store.list_assignments(repo))

    @app.post('/api/lessons/{lesson_id}/homework', response_model=Assignment)
    async def offer(lesson_id: UUID):
        enabled()
        return owned(store.offer, str(lesson_id))

    @app.get('/api/homework/{assignment_id}', response_model=Assignment)
    async def saved_assignment(assignment_id: UUID):
        enabled()
        return owned(store.get, str(assignment_id))

    @app.post('/api/homework/{assignment_id}/invitation-audio')
    async def invitation_audio(assignment_id: UUID):
        enabled()
        assignment = owned(store.get, str(assignment_id))
        if assignment.status != 'offered':
            raise HTTPException(409, 'This invitation has already been answered.')
        # Separate conversational act: never appended to terminal feedback.
        return await app.state.homework_speech('Would you like a short listening assignment about something you’re interested in?', 'homework-invitation-v1')

    @app.post('/api/homework/{assignment_id}/decision', response_model=Assignment)
    async def decide(assignment_id: UUID, body: Decision):
        enabled()
        identifier = str(assignment_id)
        existing = owned(store.get, identifier)
        lesson = repo.lesson(existing.lesson_id)
        if lesson is None or lesson.status != 'completed':
            raise HTTPException(409, 'Finish your lesson before choosing optional homework.')
        if not body.accept:
            return owned(store.reserve, identifier, False, None, settings.homework_daily_search_limit)[0]
        try:
            plan = search_plan(repo.profile(), lesson, body.topic, settings.homework_region)
        except ValueError:
            raise HTTPException(422, 'Choose a topic using English words.') from None
        existing, token = owned(store.reserve, identifier, body.accept, plan.topic, settings.homework_daily_search_limit)
        if not token:
            return existing
        try:
            # One page, no retries. A timeout never touches the completed lesson.
            contents = await asyncio.wait_for(engine.discover(plan), timeout=10)
            candidates = sorted((item for item in contents if engine.provider.validate(item, plan)),
                key=lambda item: (-relevance(item, plan), item.duration_seconds, item.provider_id))
        except (DiscoveryUnavailable, TimeoutError):
            candidates = []
        return owned(store.finish, identifier, token, candidates)

    @app.post('/api/homework/{assignment_id}/decision/attempts', response_model=Assignment)
    async def spoken(assignment_id: UUID, audio: UploadFile):
        try:
            enabled()
            # Repeated delivered events return durable state without paying for STT.
            async with lock:
                existing = owned(store.get, str(assignment_id))
                if existing.status != 'offered' or existing.discovery_in_progress:
                    return existing
                data, filename = await validate_upload(audio, settings)
                try:
                    transcription = Transcription.model_validate(await asyncio.wait_for(ai().transcribe(data, filename), timeout=15))
                except Exception:
                    raise HTTPException(503, 'I could not hear your choice. Try again or use the buttons. Your lesson is saved.') from None
                if not reliable_recognition(transcription, settings.min_transcription_confidence):
                    raise HTTPException(422, 'I could not clearly hear your choice. Try again or use the buttons. Your lesson is saved.')
                choice = spoken_decision(transcription.text)
                if choice is None:
                    raise HTTPException(422, 'Say yes please, no thanks, or yes about a topic. You can also use the buttons.')
            # reserve() is transactional and rechecks ownership/state after STT.
            return await decide(assignment_id, choice)
        finally:
            await audio.close()

    @app.post('/api/homework/{assignment_id}/confirmation-audio')
    async def confirmation_audio(assignment_id: UUID):
        enabled()
        assignment = owned(store.get, str(assignment_id))
        if assignment.status != 'assigned' or assignment.content is None:
            raise HTTPException(409, 'There is no assigned listening homework to confirm.')
        # Only application-owned objective copy is spoken, never provider metadata.
        lesson = repo.lesson(assignment.lesson_id)
        text = 'Your optional listening is ready in Plan. ' + objective(lesson.focus) + ' Your lesson is saved.'
        return await app.state.homework_speech(text, 'homework-confirmation-v1')

    @app.post('/api/homework/{assignment_id}/status', response_model=Assignment)
    async def status(assignment_id: UUID, body: StatusChange):
        enabled()
        return owned(store.change_status, str(assignment_id), body.status)

    @app.post('/api/homework/{assignment_id}/validate', response_model=Assignment)
    async def validate(assignment_id: UUID):
        enabled()
        identifier = str(assignment_id)
        assignment = owned(store.get, identifier)
        content = assignment.content
        if assignment.status != 'assigned' or content is None:
            return assignment
        lesson = repo.lesson(assignment.lesson_id)
        plan = search_plan(repo.profile(), lesson, assignment.topic or content.topic, settings.homework_region)
        # Eligibility includes each provider's canonical URL allowlist. Assignment
        # logic never follows URLs or depends on YouTube-specific identifiers.
        if not engine.provider.validate(content, plan):
            return owned(store.revalidated, identifier, None)
        if content.validated_at > utcnow() - timedelta(hours=1):
            return assignment
        owned(store.admit_validation, settings.homework_daily_search_limit)
        try:
            contents = await asyncio.wait_for(engine.provider.metadata([content.provider_id], plan.topic), timeout=5)
        except (DiscoveryUnavailable, TimeoutError):
            raise HTTPException(503, 'The video could not be checked. Please try later; your lesson is saved.') from None
        selected = next((item for item in contents if item.provider_id == content.provider_id and engine.provider.validate(item, plan)), None)
        return owned(store.revalidated, identifier, selected)
