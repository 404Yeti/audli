"""Tutor-only TTS routing. Passage synthesis and all learner state stay delegated."""
import asyncio
from contextvars import ContextVar
from hashlib import sha256
import json
import logging
import math
from time import perf_counter

import httpx

from app.audio import sanitize_generated_audio, signature_matches
from app.config import Settings
from app.services.provider import AIProvider

tts_measurements: ContextVar[list[dict] | None] = ContextVar('tts_measurements', default=None)
MAX_TUTOR_CHARACTERS = 4000
MAX_TUTOR_BYTES = 2 * 1024 * 1024


def tutor_cache_signature(settings: Settings) -> str:
    """Exclude credentials; include every audio-affecting setting and fallback."""
    config = {'version': 1, 'provider': settings.provider, 'tutor': settings.tutor_tts_provider,
        'openai_model': settings.speech_model, 'openai_voice': settings.voice,
        'fallback_voice': 'coral', 'rate': .9, 'instructions_version': 1}
    if settings.tutor_tts_provider == 'elevenlabs':
        config.update(model=settings.elevenlabs_model, voice=settings.elevenlabs_voice_id,
            stability=settings.elevenlabs_stability, similarity=settings.elevenlabs_similarity_boost,
            style=settings.elevenlabs_style, speaker_boost=settings.elevenlabs_speaker_boost)
    return sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]


async def sanitize_tutor_audio(data: bytes, kind: str, provider: AIProvider) -> bytes:
    # The router validates/sanitizes before fallback selection, avoiding two encodes.
    if isinstance(provider, TutorTTSProvider):
        return data
    return await sanitize_generated_audio(data, kind)


def tutor_cue_cache_id(settings: Settings, cue_id: str, text: str) -> str:
    # Bounded for the existing VARCHAR(80), including any valid client cue ID.
    identity = cue_id + ':' + tutor_cache_signature(settings) + ':' + text
    prefix = 'feedback-v0.2.1-' if cue_id == 'feedback' else 'cue-v1-'
    return prefix + sha256(identity.encode()).hexdigest()[:24]


def emit(metrics: dict) -> None:
    # Constructed numeric/enumerated fields only: no text, IDs, URLs or exceptions.
    logging.getLogger('uvicorn.error').info('Tutor TTS %s', json.dumps(metrics, sort_keys=True))
    collector = tts_measurements.get()
    if collector is not None:
        collector.append(dict(metrics))


def emit_configuration(settings: Settings) -> None:
    """Startup evidence without secret values, learner IDs or arbitrary strings."""
    emit({'event': 'configuration', 'provider': settings.tutor_tts_provider,
        'passage_provider': settings.provider,
        'elevenlabs_model': settings.elevenlabs_model,
        'audli_voice_selected': settings.elevenlabs_voice_id == 'yKYzqEa22xh5PdidhN70',
        'coral_selected': settings.provider == 'openai' and settings.tutor_tts_provider == 'openai' and settings.voice == 'coral',
        'passage_coral_selected': settings.provider == 'openai' and settings.voice == 'coral',
        'coral_fallback_selected': settings.tutor_tts_provider == 'elevenlabs',
        'openai_speech_model_matches': settings.speech_model == 'gpt-4o-mini-tts',
        'elevenlabs_key_configured': bool(settings.elevenlabs_api_key and settings.elevenlabs_api_key.get_secret_value().strip()),
        'openai_key_configured': bool(settings.openai_api_key and settings.openai_api_key.get_secret_value().strip()),
        'timeout_seconds': settings.tutor_tts_timeout_seconds,
        'fallback_timeout_seconds': settings.tutor_tts_fallback_timeout_seconds,
        'stability': settings.elevenlabs_stability,
        'similarity_boost': settings.elevenlabs_similarity_boost,
        'style': settings.elevenlabs_style,
        'speaker_boost': settings.elevenlabs_speaker_boost})


class TutorTTSProvider:
    def __init__(self, base: AIProvider, settings: Settings, client: httpx.AsyncClient | None = None):
        self.base, self.settings = base, settings
        self.eleven_client = client or httpx.AsyncClient(timeout=settings.tutor_tts_timeout_seconds,
            follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=0))

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def speech(self, exercise):
        return await self.base.speech(exercise)

    async def close(self):
        await self.eleven_client.aclose()
        if hasattr(self.base, 'client'):
            await self.base.client.close()

    async def eleven_speak(self, text: str, rate: float, metrics: dict) -> bytes:
        settings = self.settings
        async with self.eleven_client.stream('POST',
            f'https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream',
            headers={'xi-api-key': settings.elevenlabs_api_key.get_secret_value()},
            params={'output_format': 'mp3_44100_128'},
            json={'text': text, 'model_id': settings.elevenlabs_model,
                'voice_settings': {'stability': settings.elevenlabs_stability,
                    'similarity_boost': settings.elevenlabs_similarity_boost,
                    'style': settings.elevenlabs_style, 'use_speaker_boost': settings.elevenlabs_speaker_boost,
                    'speed': rate}}) as response:
            metrics['http_status'] = response.status_code
            cost = response.headers.get('character-cost')
            if cost is not None:
                try:
                    value = float(cost)
                    if math.isfinite(value) and value >= 0:
                        metrics['reported_cost_units'] = value
                except ValueError:
                    pass
            if response.status_code != 200:
                raise ValueError('Tutor synthesis rejected')
            audio = bytearray()
            async for chunk in response.aiter_bytes():
                if chunk:
                    if 'first_audio_byte_ms' not in metrics:
                        metrics['first_audio_byte_ms'] = (perf_counter() - metrics['_start']) * 1000
                    audio.extend(chunk)
                if len(audio) > MAX_TUTOR_BYTES:
                    raise ValueError('Tutor audio exceeds size limit')
            return bytes(audio)

    async def attempt(self, provider: str, text: str, rate: float, fallback: bool) -> bytes:
        started = perf_counter()
        metrics = {'provider': provider, 'fallback': fallback, 'input_characters': len(text),
            'first_audio_byte_ms': None, 'reported_cost_units': None, 'retries': 0}
        try:
            timeout = self.settings.tutor_tts_timeout_seconds if provider == 'elevenlabs' else self.settings.tutor_tts_fallback_timeout_seconds
            async with asyncio.timeout(timeout):
                if provider == 'elevenlabs':
                    metrics.pop('first_audio_byte_ms')
                    metrics['_start'] = started
                    data = await self.eleven_speak(text, rate, metrics)
                elif hasattr(self.base, 'tutor_speak'):
                    data = await self.base.tutor_speak(text, rate, metrics)
                else:
                    data = await self.base.speak(text, rate)
                metrics['synthesis_ms'] = (perf_counter() - started) * 1000
                if not data or len(data) > MAX_TUTOR_BYTES or not signature_matches(data, 'mp3'):
                    raise ValueError('Tutor audio is invalid')
                data = await sanitize_generated_audio(data, 'mp3')
            metrics['outcome'] = 'success'
            return data
        except asyncio.CancelledError:
            metrics['outcome'] = 'cancelled'
            raise
        except Exception:
            metrics['outcome'] = 'failure'
            # Suppress payload-bearing SDK errors, including chained exceptions.
            raise ValueError('Tutor speech temporarily unavailable') from None
        finally:
            metrics.pop('_start', None)
            metrics['ready_ms'] = (perf_counter() - started) * 1000
            emit(metrics)

    async def speak(self, text: str, speech_rate: float = .9) -> bytes:
        if not text.strip() or len(text) > MAX_TUTOR_CHARACTERS or not math.isfinite(speech_rate) or not .7 <= speech_rate <= 1.2:
            raise ValueError('Invalid tutor speech input')
        if self.settings.tutor_tts_provider == 'elevenlabs':
            try:
                return await self.attempt('elevenlabs', text, speech_rate, False)
            except asyncio.CancelledError:
                raise
            except ValueError:
                emit({'event': 'fallback', 'provider': 'openai', 'retries': 0})
                return await self.attempt('openai', text, speech_rate, True)
        return await self.attempt('openai', text, speech_rate, False)
