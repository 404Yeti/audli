"""Optional owned planning check-in; audio is generated only when requested."""
from fastapi import HTTPException, UploadFile, Form
from pydantic import ValidationError
from app.onboarding import Revision, ProfileExtraction
from app.audio import validate_upload
from app.models import Transcription
from app.recognition import reliable_recognition
from app.planner import CHECKIN_PROMPT, requested_focus, qualifying_count
from app.storage.planner import due


def register_planner_routes(app, repo, ai, lock, settings, speech):
    def public(roadmap):
        open_lesson = repo.lesson()
        ready = repo.profile().onboarding_status == 'complete'
        milestone = due(roadmap)
        return {'revision': roadmap.revision, 'completed_lessons': len(roadmap.completed_ids),
            'block_progress': qualifying_count(roadmap) % 3,
            'focus': roadmap.upcoming[0].objective, 'upcoming': roadmap.upcoming,
            'completed': [{'id': item.id, 'focus': item.focus, 'completed_at': item.completed_at,
                           'title': item.plan_topic or 'Listening lesson'} for item in repo.accent_lesson_history() if item.status == 'completed'],
            'readiness': 'onboarding' if not ready else 'resume_lesson' if open_lesson else 'ready',
            'checkin': {'after_lesson': milestone, 'status': 'due' if milestone else 'answered' if roadmap.feedback and roadmap.feedback[-1].status == 'answered' else 'skipped' if roadmap.feedback else 'not_due',
                'available': bool(milestone and ready and not open_lesson), 'prompt': CHECKIN_PROMPT if milestone else None,
                'pending': roadmap.pending}}

    def enabled():
        if not settings.planner_enabled:
            raise HTTPException(503, 'Your listening plan is temporarily unavailable. You can continue ordinary lessons.')

    def save(**kwargs):
        enabled()
        try:
            return repo.roadmap(**kwargs)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    def check(revision):
        enabled()
        roadmap = repo.roadmap()
        if roadmap.revision != revision or not public(roadmap)['checkin']['available']:
            raise HTTPException(409, 'Your plan changed. Finish your lesson or reload the plan.')
        return roadmap

    @app.get('/api/plan')
    async def get_plan():
        enabled()
        return public(repo.roadmap())

    @app.post('/api/plan/checkin/audio')
    async def checkin_audio(body: Revision):
        check(body.revision)
        response = await speech(CHECKIN_PROMPT, 'planner-checkin-v1')
        check(body.revision)
        return response

    @app.post('/api/plan/checkin/attempts')
    async def attempt(audio: UploadFile, revision: int = Form(ge=0)):
        try:
            async with lock:
                check(revision)
                data, filename = await validate_upload(audio, settings)
                pending = Transcription.model_validate(await ai().transcribe(data, filename))
                return public(save(revision=revision, pending=pending))
        finally:
            await audio.close()

    @app.post('/api/plan/checkin/answer')
    async def answer(body: Revision):
        async with lock:
            roadmap = check(body.revision)
            if roadmap.pending is None:
                raise HTTPException(409, 'Record your check-in response first.')
            if not reliable_recognition(roadmap.pending, settings.min_transcription_confidence):
                raise HTTPException(422, 'Recognition is uncertain. Record again or skip this check-in.')
            text = roadmap.pending.text
            try:
                preferences = ProfileExtraction.model_validate(await ai().extract_profile('interests', text))
            except (ValidationError, ValueError):
                raise HTTPException(422, 'Your response is saved. Retry or skip this check-in.') from None
            # Persist feedback and next outlines before any coaching audio.
            return public(save(revision=body.revision, feedback={'status': 'answered', 'response': text,
                'interests': preferences.interests, 'focus': requested_focus(text)}))

    @app.post('/api/plan/checkin/skip')
    async def skip(body: Revision):
        async with lock:
            check(body.revision)
            return public(save(revision=body.revision, feedback={'status': 'skipped'}))
