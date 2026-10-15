"""Account-scoped, restartable turn-based onboarding; recordings never persist."""
from fastapi import HTTPException, UploadFile, Form
from fastapi.responses import Response
from hashlib import sha256
from app.audio import validate_upload
from app.services.tutor_tts import TutorSpeech
from app.models import Transcription
from app.diagnostics import operation, timed_lock
from app.recognition import reliable_recognition
from app.onboarding import Revision, WalkthroughUpdate, Answer, TypedName, GoalAnswer, apply_goal, name_entry_available, apply_extraction, apply_accent_answer, application_destination, onboarding_prompt


def register_onboarding_routes(app, repo, ai, lock, settings):
    def walkthrough_available(profile):
        return (profile.onboarding_status == 'not_started' and profile.onboarding.stage == 'identity'
                and not profile.onboarding.welcome and not profile.onboarding.pending
                and profile.completed_attempts == 0 and not profile.onboarding.answered_fields
                and type(profile.onboarding.walkthrough) is int)

    def public(profile):
        return {'profile': profile, 'stage': profile.onboarding.stage,
                'revision': profile.onboarding.revision,
                'welcome': profile.onboarding.welcome,
                'walkthrough': profile.onboarding.walkthrough if walkthrough_available(profile) else None,
                'name_entry_available': name_entry_available(profile),
                'goal_entry_available': profile.onboarding_status == 'in_progress' and profile.onboarding.stage == 'needs',
                'goal_question_revision': profile.onboarding.goal_question_revision if profile.onboarding.goal_question_revision is not None else 0,
                'name_question_revision': profile.onboarding.name_question_revision,
                'prompt': onboarding_prompt(profile),
                'clarification_paused': profile.onboarding.clarification_paused,
                'pending': profile.onboarding.pending,
                'destination': application_destination(profile)}

    def check(revision):
        profile = repo.profile()
        if profile.onboarding_status == 'complete' or profile.onboarding.revision != revision:
            raise HTTPException(409, 'Onboarding changed. Reload your saved progress before continuing.')
        return profile

    def save(profile, revision):
        try:
            with operation(settings, 'onboarding.profile_persistence'):
                return public(repo.save_onboarding(profile, revision))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def require_active(profile):
        if profile.onboarding.clarification_paused:
            raise HTTPException(409, 'Pause and check your microphone, then retry the conversation.')

    @app.post('/api/onboarding/retry-clarification')
    async def retry_clarification(body: Revision):
        async with lock:
            profile = check(body.revision)
            if not profile.onboarding.clarification_paused:
                return public(profile)
            profile.onboarding.clarification_paused = False
            profile.onboarding.clarification_count = 0
            return save(profile, body.revision)

    @app.get('/api/onboarding')
    async def state():
        return public(repo.profile())

    @app.post('/api/onboarding/walkthrough')
    async def walkthrough(body: WalkthroughUpdate):
        async with lock:
            profile = repo.profile()
            if not walkthrough_available(profile):
                return public(profile)
            profile = check(body.revision)
            profile.onboarding.walkthrough = body.step
            return save(profile, body.revision)

    @app.post('/api/onboarding/start')
    async def start(body: Revision):
        async with lock:
            profile = repo.profile()
            if profile.onboarding_status != 'not_started' and not (
                    profile.onboarding_status == 'profile_saved' and profile.onboarding.stage == 'identity'
                    and profile.onboarding.revision == 0):
                return public(profile)
            profile = check(body.revision)
            if profile.onboarding_status == 'not_started' and not profile.onboarding.welcome:
                profile.onboarding.welcome = 'introduction'
            has_saved_name = profile.name != 'Listener' or profile.onboarding_status == 'profile_saved'
            profile.onboarding_status = 'in_progress'
            if has_saved_name:
                profile.onboarding.name_question_revision = None
                profile.onboarding.answered_fields = ['name']
                profile.onboarding.clarification = 'target_language'
            return save(profile, body.revision)

    @app.post('/api/onboarding/heard')
    async def heard(body: Revision):
        async with lock:
            profile = check(body.revision)
            if not profile.onboarding.welcome or profile.onboarding_status != 'in_progress':
                raise HTTPException(409, 'Continue from your saved onboarding question.')
            profile.onboarding.welcome = 'status' if profile.onboarding.welcome == 'introduction' else None
            if profile.onboarding.welcome is None and 'name' not in profile.onboarding.answered_fields:
                profile.onboarding.name_question_revision = body.revision + 1
            return save(profile, body.revision)

    @app.post('/api/onboarding/name')
    async def typed_name(body: TypedName):
        async with lock:
            profile = repo.profile()
            progress = profile.onboarding
            # The question token survives transcription/identity extraction so a
            # submitted typed answer wins even if voice acquired the lock first.
            if (profile.onboarding_status != 'in_progress' or progress.welcome
                    or progress.name_question_revision != body.question_revision
                    or progress.stage not in ('identity', 'needs')):
                raise HTTPException(409, 'Continue from your saved onboarding question.')
            if progress.typed_name:
                if profile.name == body.name:
                    return public(profile)  # Lost response: no second advancement.
                raise HTTPException(409, 'Your name is already saved. Reload your progress.')
            revision = progress.revision
            progress.typed_name = True
            profile.name = body.name
            progress.pending = None
            progress.clarification_paused = False
            progress.clarification_count = 0
            if progress.stage == 'identity':
                # Satisfy the name question; preserve any language still to ask.
                if 'name' not in progress.answered_fields:
                    progress.answered_fields.append('name')
                profile = apply_extraction(profile, dict(name=None, target_language=None,
                    goal=None, target_situations=None, interests=None))
            return save(profile, revision)

    @app.post('/api/onboarding/goal')
    async def goal(body: GoalAnswer):
        async with lock:
            profile = repo.profile()
            progress = profile.onboarding
            token = progress.goal_question_revision
            if token is None and progress.stage == 'needs':
                token = 0  # Stable token for needs checkpoints from older releases.
            if (profile.onboarding_status != 'in_progress' or progress.welcome
                    or token != body.question_revision or progress.stage not in ('needs', 'interests')):
                raise HTTPException(409, 'Continue from your saved onboarding question.')
            if not body.skip and not body.text:
                raise HTTPException(422, 'Type your goal or choose Skip for now.')
            if progress.typed_goal:
                return public(profile)  # First explicit submission wins, including retries.
            revision = progress.revision
            profile = apply_goal(profile, None if body.skip else body.text)
            profile.onboarding.typed_goal = True
            return save(profile, revision)

    @app.post('/api/onboarding/attempts')
    async def transcribe(audio: UploadFile, revision: int = Form(ge=0)):
        try:
            async with timed_lock(settings, lock, 'onboarding.upload'):
                profile = check(revision)
                require_active(profile)
                if profile.onboarding_status != 'in_progress' or profile.onboarding.stage == 'review' or profile.onboarding.welcome:
                    raise HTTPException(409, 'Resume your current onboarding stage.')
                with operation(settings, 'onboarding.upload_validation'):
                    data, filename = await validate_upload(audio, settings)
                with operation(settings, 'onboarding.transcription'):
                    try:
                        transcription = Transcription.model_validate(await ai().transcribe(data, filename))
                    except Exception:
                        raise HTTPException(503, 'I had trouble transcribing your recording. Please try again.') from None
                if profile.onboarding.stage == 'needs':
                    profile.onboarding.name_question_revision = None
                    if profile.onboarding.goal_question_revision is None:
                        profile.onboarding.goal_question_revision = 0
                elif profile.onboarding.stage == 'interests':
                    profile.onboarding.goal_question_revision = None
                profile.onboarding.pending = transcription
                return save(profile, revision)
        finally:
            await audio.close()

    @app.post('/api/onboarding/answer')
    async def answer(body: Answer):
        async with timed_lock(settings, lock, 'onboarding.extraction'):
            profile = check(body.revision)
            require_active(profile)
            if profile.onboarding.pending is None:
                raise HTTPException(409, 'Record an answer before confirming recognition.')
            if body.hands_free and (body.text.strip() != profile.onboarding.pending.text.strip()
                    or not reliable_recognition(profile.onboarding.pending, settings.min_transcription_confidence)):
                raise HTTPException(422, "I didn't quite catch that. Could you say it again?")
            if not body.text.strip():
                raise HTTPException(422, 'Please check recognition or record again.')
            if profile.onboarding.stage == 'needs' and body.text.strip().casefold().rstrip('.!?') in ('skip', 'skip for now', 'no preference', 'not sure', "i don’t know", "i don't know"):
                return save(apply_goal(profile), body.revision)
            if profile.onboarding.stage == 'accents':
                return save(apply_accent_answer(profile, body.text.strip()), body.revision)
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
            profile.onboarding.answered_fields = ['name']
            profile.onboarding.name_question_revision = None
            profile.onboarding.clarification = 'target_language'
            profile.onboarding.clarification_count = 0
            profile.onboarding.clarification_paused = False
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
            require_active(profile)
            text = onboarding_prompt(profile)
            tutor = TutorSpeech(settings, ai(), profile.tutor_voice)
            kind = tutor.kind
            # Only application-owned static prompts are reused. Include the complete
            # voice configuration so changed settings cannot replay old speech.
            key = ':'.join((str(repo.learner_id), 'onboarding-prompt-v1', settings.provider,
                            settings.speech_model, settings.voice, tutor.signature, text))
            name = sha256(key.encode()).hexdigest() + '.' + kind
            data = repo.audio_blob(name)
            if data is None:
                with operation(settings, 'onboarding.tts_provider'):
                    data = await tutor.speak(text)
                with operation(settings, 'onboarding.tts_sanitization'):
                    data = await tutor.sanitize(data)
                repo.cache_lesson_audio(name, data)
            return Response(data, media_type='audio/wav' if kind == 'wav' else 'audio/mpeg')
