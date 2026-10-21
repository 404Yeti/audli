"""Authenticated settings and cached, synthesis-free tutor voice previews."""
import asyncio
from hashlib import sha256
from fastapi import HTTPException
from fastapi.responses import Response
from app.tutor_voice import TutorVoice, TutorVoiceUpdate, VOICE_LABELS, tutor_settings
from app.services.tutor_preview import existing_preview


def register_tutor_voice_routes(app, repo, lock, settings, new_lock):
    preview_lock = new_lock()

    def public(voice):
        return {'voice': voice, 'available': settings.tutor_tts_provider == 'elevenlabs',
                'options': [{'value': key, 'label': label} for key, label in VOICE_LABELS.items()]}

    @app.get('/api/profile/tutor-voice')
    async def preference():
        return public(repo.profile().tutor_voice)

    @app.put('/api/profile/tutor-voice')
    async def update(body: TutorVoiceUpdate):
        async with lock:
            return public(repo.save_tutor_voice(body.voice))

    @app.get('/api/profile/tutor-voice/preview/{voice}')
    async def preview(voice: TutorVoice):
        if settings.tutor_tts_provider != 'elevenlabs' or not settings.elevenlabs_api_key:
            raise HTTPException(503, 'Voice previews are temporarily unavailable. Your preference is still saved.')
        config = tutor_settings(settings, voice)
        voice_id = config.elevenlabs_voice_id
        name = sha256(f'{repo.learner_id}:tutor-preview-v1:{voice_id}'.encode()).hexdigest() + '.mp3'
        async with preview_lock:
            data = repo.audio_blob(name)
            if data is None:
                app.state.limits.admit('practice', 'tutor.preview')  # Saved previews replay freely.
                try:
                    data = await existing_preview(config, voice_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise HTTPException(503, 'This voice preview is unavailable. You can still save your preference and try again later.') from None
                repo.cache_lesson_audio(name, data)
        return Response(data, media_type='audio/mpeg')
