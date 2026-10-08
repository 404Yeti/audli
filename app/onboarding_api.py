"""Account-scoped, restartable turn-based onboarding; recordings never persist."""
from fastapi import HTTPException, UploadFile, Form
from fastapi.responses import Response
from app.audio import validate_upload, sanitize_generated_audio
from app.models import Transcription
from app.diagnostics import operation, timed_lock
from app.recognition import reliable_recognition
from app.onboarding import PROMPTS, Revision, Answer, apply_extraction, application_destination


def register_onboarding_routes(app, repo, ai, lock, settings):
    def public(profile):
        return {'profile': profile, 'stage': profile.onboarding.stage,
                'revision': profile.onboarding.revision,
                'prompt': PROMPTS[profile.onboarding.stage],
                'pending': profile.onboarding.pending,
                'destination': application_destination(profile)}

    def check(revision):
        profile = repo.profile()
        if profile.onboarding_status == 'complete' or profile.onboarding.revision != revision:
            raise HTTPException(409, 'Onboarding changed. Reload your saved progress before continuing.')
        return profile

    def save(profile, revision):
        try:
            return public(repo.save_onboarding(profile, revision))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/onboarding')
    async def state():
        return public(repo.profile())

    @app.post('/api/onboarding/start')
    async def start(body: Revision):
        async with lock:
            profile = repo.profile()
            if profile.onboarding_status != 'not_started' and not (
                    profile.onboarding_status == 'profile_saved' and profile.onboarding.stage == 'identity'
                    and profile.onboarding.revision == 0):
                return public(profile)
            profile = check(body.revision)
            profile.onboarding_status = 'in_progress'
            return save(profile, body.revision)

    @app.post('/api/onboarding/attempts')
    async def transcribe(audio: UploadFile, revision: int = Form(ge=0)):
        try:
            async with timed_lock(settings, lock, 'onboarding.upload'):
                profile = check(revision)
                if profile.onboarding_status != 'in_progress' or profile.onboarding.stage == 'review':
                    raise HTTPException(409, 'Resume your current onboarding stage.')
                with operation(settings, 'onboarding.upload_validation'):
                    data, filename = await validate_upload(audio, settings)
                with operation(settings, 'onboarding.transcription'):
                    transcription = Transcription.model_validate(await ai().transcribe(data, filename))
                profile.onboarding.pending = transcription
                return save(profile, revision)
        finally:
            await audio.close()

    @app.post('/api/onboarding/answer')
    async def answer(body: Answer):
        async with timed_lock(settings, lock, 'onboarding.extraction'):
            profile = check(body.revision)
            if profile.onboarding.pending is None:
                raise HTTPException(409, 'Record an answer before confirming recognition.')
            if body.hands_free and (body.text.strip() != profile.onboarding.pending.text.strip()
                    or not reliable_recognition(profile.onboarding.pending, settings.min_transcription_confidence)):
                raise HTTPException(422, "I didn't quite catch that. Could you say it again?")
            if not body.text.strip():
                raise HTTPException(422, 'Please check recognition or record again.')
            with operation(settings, 'onboarding.extraction_provider'):
                extraction = await ai().extract_profile(profile.onboarding.stage, body.text.strip())
            try:
                profile = apply_extraction(profile, extraction)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return save(profile, body.revision)

    @app.post('/api/onboarding/revise')
    async def revise(body: Revision):
        async with lock:
            profile = check(body.revision)
            if profile.onboarding.stage != 'review':
                raise HTTPException(409, 'Finish the current stage first.')
            profile.onboarding.stage = 'identity'
            profile.onboarding_status = 'in_progress'
            return save(profile, body.revision)

    @app.post('/api/onboarding/complete')
    async def complete(body: Revision):
        async with lock:
            profile = repo.profile()
            if profile.onboarding_status == 'complete':
                return public(profile)  # Lost response / duplicate completion.
            profile = check(body.revision)
            if profile.onboarding_status != 'profile_saved' or profile.onboarding.stage != 'review':
                raise HTTPException(409, 'Finish capturing your listening preferences first.')
            profile.onboarding_status = 'complete'
            return save(profile, body.revision)

    @app.post('/api/onboarding/audio')
    async def speech(body: Revision):
        async with timed_lock(settings, lock, 'onboarding.audio'):
            profile = check(body.revision)
            text = PROMPTS[profile.onboarding.stage]
            kind = 'wav' if settings.provider == 'demo' else 'mp3'
            with operation(settings, 'onboarding.tts_provider'):
                data = await ai().speak(text)
            with operation(settings, 'onboarding.tts_sanitization'):
                data = await sanitize_generated_audio(data, kind)
            return Response(data, media_type='audio/wav' if kind == 'wav' else 'audio/mpeg')
