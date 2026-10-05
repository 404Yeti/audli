"""Explicit paid smoke check; filename is excluded from normal test discovery.
Run only when requested: pytest -q tests/live_generation.py -s
"""
import asyncio
import httpx
import pytest
from app.config import Settings
from app.main import create_app
from app.repository import ProgressRepository


def test_live_post_exercises(tmp_path):
    settings = Settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        pytest.skip('Live OpenAI key not visible to this process')
    if settings.provider != 'openai':
        pytest.skip('Live exercise verification requires AUDLI_PROVIDER=openai')
    profile = ProgressRepository(settings.data_dir/'audli.sqlite3').profile()
    isolated = settings.model_copy(update={'data_dir': tmp_path, 'environment': 'development'})
    app = create_app(isolated)
    app.state.repository.save_profile(profile)
    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                result = await client.post('/api/exercises')
                print('Live POST /api/exercises status:', result.status_code, flush=True)
                assert result.status_code == 200, result.text
                fields = set(result.json())
                assert fields == {'id', 'difficulty', 'audio_url', 'completed_attempt_id'}
                audio = await client.get(result.json()['audio_url'])
                assert audio.status_code == 200 and len(audio.content) > 1000
                transcript = await client.get('/api/exercises/'+result.json()['id']+'/transcript')
                assert transcript.status_code == 403
                print('Live exercise generated and persisted; audio served; transcript gate=403.', flush=True)
    asyncio.run(check())
