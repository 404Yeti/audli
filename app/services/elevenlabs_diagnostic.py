"""Temporary release diagnostic. Remove after the controlled verification."""
import asyncio
import http.client
import json
import logging
import os
from pathlib import Path

from app.config import Settings

_LOG = logging.getLogger('uvicorn.error')
_CATEGORIES = {
    'invalid_api_key': 'invalid_api_key',
    'quota_exceeded': 'quota_exceeded',
    'missing_permissions': 'missing_permissions',
    'voice_not_found': 'voice_not_found',
    'paid_plan_required': 'paid_plan_required',
    'detected_unusual_activity': 'unusual_activity',
}


def _probe(settings: Settings) -> None:
    status, category = 0, 'network_failure'
    connection = None
    try:
        key = settings.elevenlabs_api_key
        if not key or not key.get_secret_value().strip():
            category = 'missing_credentials'
            return
        connection = http.client.HTTPSConnection('api.elevenlabs.io', timeout=15)
        payload = json.dumps({'text': 'Hello.', 'model_id': settings.elevenlabs_model,
            'voice_settings': {'stability': settings.elevenlabs_stability,
                'similarity_boost': settings.elevenlabs_similarity_boost,
                'style': settings.elevenlabs_style,
                'use_speaker_boost': settings.elevenlabs_speaker_boost, 'speed': .9}})
        connection.request('POST',
            f'/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream?output_format=mp3_44100_128',
            body=payload, headers={'xi-api-key': key.get_secret_value(), 'Content-Type': 'application/json'})
        response = connection.getresponse()
        status = response.status
        category = 'accepted' if status == 200 else 'provider_rejected'
        if status != 200:
            try:
                body = json.loads(response.read(16385))
                detail = body.get('detail')
                code = detail.get('status') if isinstance(detail, dict) else None
                category = _CATEGORIES.get(code, 'provider_rejected') if isinstance(code, str) else 'provider_rejected'
            except Exception:
                pass
        # Success audio is neither read, saved nor logged. No redirect or retry.
    except Exception:
        category = 'network_failure'
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        _LOG.info('ElevenLabs diagnostic http_status=%s category=%s', status, category)


def schedule(settings: Settings) -> asyncio.Task | None:
    """No-op unless explicitly enabled for this exact Render revision."""
    revision = os.environ.get('RENDER_GIT_COMMIT', '')
    if (os.environ.get('AUDLI_ELEVENLABS_DIAGNOSTIC_ENABLED') != 'true'
            or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision)
            or os.environ.get('AUDLI_ELEVENLABS_DIAGNOSTIC_REVISION') != revision):
        return None
    try:
        # Shared by workers in this container; claim BEFORE sending, never retry.
        marker = Path('/tmp') / f'audli-elevenlabs-diagnostic-{revision}.once'
        with marker.open('x'):
            pass
        return asyncio.create_task(asyncio.to_thread(_probe, settings))
    except Exception:
        # Diagnostics must not fail startup, even on a read-only filesystem.
        return None
