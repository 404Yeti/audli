"""AUD-74: ordinary (non-accent) passages synthesized with the tutor's configured ElevenLabs voice ID.

Off by default. Accent-specific voices are handled before this module and are untouched. Admission uses
the shared fleet character budget and concurrency limit; a refusal or provider failure falls back to the
OpenAI passage voice explicitly, with a fixed-category reason in telemetry (never script text or secrets)."""
import asyncio
import json
import logging
import math
from time import perf_counter

import httpx

from app.models import PassageVoice

MAX_PASSAGE_BYTES = 8 * 1024 * 1024
_SPEED_RANGE = (.7, 1.2)


def emit(event: dict) -> None:
    # Constructed enumerated/numeric fields only: no script, learner IDs, URLs, exceptions or secrets.
    logging.getLogger('uvicorn.error').info('Passage TTS %s', json.dumps(event, sort_keys=True))


def emit_default_passage_configuration(settings) -> None:
    emit({'event': 'configuration', 'default_passage_elevenlabs_enabled': settings.default_passage_elevenlabs_enabled,
        'voice_id': settings.elevenlabs_voice_id if settings.default_passage_elevenlabs_enabled else None,
        'model': settings.passage_tts_model, 'timeout_seconds': settings.passage_tts_timeout_seconds,
        'max_concurrent': settings.passage_tts_max_concurrent, 'daily_characters': settings.passage_tts_daily_characters})


async def default_passage_audio(settings, provider, content, limits, *, fallback=False):
    started = perf_counter()
    characters = len(content.script)
    voice_id = settings.elevenlabs_voice_id
    event = {'event': 'default_passage', 'provider': 'elevenlabs', 'voice_id': voice_id,
        'model': settings.passage_tts_model, 'characters': characters, 'accent_fallback': fallback}
    reason, reservation, outcome = None, None, 'failed'
    if not (math.isfinite(content.speech_rate) and _SPEED_RANGE[0] <= content.speech_rate <= _SPEED_RANGE[1]):
        reason = 'unsupported_rate'
    elif (settings.provider == 'demo' or not settings.elevenlabs_api_key
            or not settings.elevenlabs_api_key.get_secret_value().strip()):
        reason = 'missing_credentials'
    elif limits is None:
        reason = 'admission_unavailable'
    else:
        admitted = await limits.call(limits.reserve_default, characters)
        if admitted is None:
            reason = 'control_unavailable'
        elif isinstance(admitted, str):
            reason = admitted
        else:
            reservation = admitted
    if reason is None:
        try:
            from app.audio import sanitize_generated_audio, signature_matches
            from app.services.tutor_tts import failure_reason, track_elevenlabs_inflight

            async def generate():
                async with httpx.AsyncClient(timeout=settings.passage_tts_timeout_seconds, follow_redirects=False,
                        transport=httpx.AsyncHTTPTransport(retries=0)) as client:
                    # Same voice ID and delivery settings as the tutor so the one Audli voice is consistent.
                    response = await client.post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}',
                        headers={'xi-api-key': settings.elevenlabs_api_key.get_secret_value()},
                        params={'output_format': 'mp3_44100_128'},
                        json={'text': content.script, 'model_id': settings.passage_tts_model,
                            'voice_settings': {'stability': settings.elevenlabs_stability,
                                'similarity_boost': settings.elevenlabs_similarity_boost,
                                'style': settings.elevenlabs_style, 'use_speaker_boost': settings.elevenlabs_speaker_boost,
                                'speed': content.speech_rate}})
                    event['http_status'] = response.status_code
                    cost = response.headers.get('character-cost')
                    try:
                        if cost is not None and math.isfinite(float(cost)) and float(cost) >= 0:
                            event['reported_cost_units'] = float(cost)
                    except ValueError:
                        pass
                    if response.status_code != 200:
                        event['failure'] = await failure_reason(response)
                        raise ValueError('Passage synthesis rejected')
                    data = response.content
                    if not data or len(data) > MAX_PASSAGE_BYTES or not signature_matches(data, 'mp3'):
                        raise ValueError('Passage audio is invalid')
                    return await sanitize_generated_audio(data, 'mp3')

            with track_elevenlabs_inflight(settings, event):
                audio = await asyncio.wait_for(generate(), settings.passage_tts_timeout_seconds)
            outcome = 'success'
            event.update(outcome='success', fallback_reason=None, ready_ms=(perf_counter() - started) * 1000)
            emit(event)
            return audio, PassageVoice(provider='elevenlabs', voice_id=voice_id, fallback=fallback)
        except asyncio.CancelledError:
            outcome = 'cancelled'
            event.update(outcome='cancelled', ready_ms=(perf_counter() - started) * 1000)
            emit(event)
            raise
        except Exception as error:
            reason = (event.get('failure') or ('timeout' if isinstance(error, (TimeoutError, httpx.TimeoutException))
                else 'network' if isinstance(error, httpx.HTTPError) else 'invalid_audio'))
        finally:
            if reservation:
                # Characters are never refunded, matching the accent ledger.
                await asyncio.shield(limits.call(limits.finish, reservation[0], outcome))
    event.update(outcome='fallback', fallback_reason=reason, fallback_provider='openai',
        ready_ms=(perf_counter() - started) * 1000)
    logging.getLogger('uvicorn.error').warning('Passage TTS %s', json.dumps(event, sort_keys=True))
    return await provider.speech(content), PassageVoice(provider=settings.provider,
        voice_id=settings.voice, fallback=fallback)
