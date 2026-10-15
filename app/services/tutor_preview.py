"""Download an existing provider preview; never synthesize or fall back to TTS."""
import asyncio
import json
from urllib.parse import urlsplit
import httpx
from app.audio import sanitize_generated_audio, signature_matches
from app.services.tutor_tts import MAX_TUTOR_BYTES


async def bounded_get(client, url, limit, headers=None):
    async with client.stream('GET', url, headers=headers) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('Preview exceeds size limit')
        return bytes(data)


async def existing_preview(settings, voice_id, client=None):
    """Only ElevenLabs' documented public preview bucket is accepted."""
    owned = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False,
                                        transport=httpx.AsyncHTTPTransport(retries=0))
    try:
        async with asyncio.timeout(15):
            metadata = json.loads(await bounded_get(client,
                f'https://api.elevenlabs.io/v1/voices/{voice_id}', 128 * 1024,
                {'xi-api-key': settings.elevenlabs_api_key.get_secret_value()}))
            preview = metadata.get('preview_url')
            if not isinstance(preview, str):
                raise ValueError('No recorded preview')
            target = urlsplit(preview)
            if (target.scheme != 'https' or target.hostname != 'storage.googleapis.com'
                    or target.port not in (None, 443) or target.username or target.password
                    or not target.path.startswith('/eleven-public-prod/')):
                raise ValueError('Unsupported preview location')
            # Never send provider credentials to the public preview bucket.
            data = await bounded_get(client, preview, MAX_TUTOR_BYTES)
            if not signature_matches(data, 'mp3'):
                raise ValueError('Invalid recorded preview')
            return await sanitize_generated_audio(data, 'mp3', max_seconds=12)
    finally:
        if owned:
            await client.aclose()
