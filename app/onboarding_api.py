"""Account-scoped, restartable turn-based onboarding; recordings never persist."""
from fastapi import HTTPException, UploadFile, Form
from fastapi.responses import Response
from app.audio import validate_upload, sanitize_generated_audio
from app.models import Transcription
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
            async with lock:
                profile = check(revision)
                if profile.onboarding_status != 'in_progress' or profile.onboarding.stage == 'review':
                    raise HTTPException(409, 'Resume your current onboarding stage.')
                data, filename = await validate_upload(audio, settings)
                transcription = Transcription.model_validate(await ai().transcribe(data, filename))
                profile.onboarding.pending = transcription
                return save(profile, revision)
        finally:
            await audio.close()

    @app.post('/api/onboarding/answer')
    async def answer(body: Answer):
        async with lock:
            profile = check(body.revision)
            if profile.onboarding.pending is None:
                raise HTTPException(409, 'Record an answer before confirming recognition.')
            if not body.text.strip():
                raise HTTPException(422, 'Please check recognition or record again.')
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
        async with lock:
            profile = check(body.revision)
            text = PROMPTS[profile.onboarding.stage]
            kind = 'wav' if settings.provider == 'demo' else 'mp3'
            data = await sanitize_generated_audio(await ai().speak(text), kind)
            return Response(data, media_type='audio/wav' if kind == 'wav' else 'audio/mpeg')
