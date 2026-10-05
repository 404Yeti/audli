"""Opt-in paid evaluation replay; all writes go to an isolated SQLite copy.
Run: source .venv/bin/activate && pytest -q tests/live_evaluation.py -s --tb=no
Never prints learner text or provider response contents.
"""
import asyncio
import json
import sqlite3
import httpx
import pytest
from app.config import Settings
from app.main import create_app

FAILED_ATTEMPT = '035dc1b7-5ff8-4b8a-85f1-6b38f899e7ab'


@pytest.mark.parametrize('_replay', range(3))
def test_live_failed_evaluation_retry(tmp_path, _replay):
    settings = Settings()
    if not settings.openai_api_key or settings.provider != 'openai':
        pytest.skip('Live OpenAI configuration unavailable')
    source_path = (settings.data_dir / 'audli.sqlite3').resolve()
    if not source_path.exists():
        pytest.skip('Original failed attempt database unavailable')
    with sqlite3.connect(f'{source_path.as_uri()}?mode=ro', uri=True) as source:
        with sqlite3.connect(tmp_path / 'audli.sqlite3') as target:
            source.backup(target)
    isolated = settings.model_copy(update={'data_dir': tmp_path, 'environment': 'development'})
    app = create_app(isolated)
    repo = app.state.repository
    attempt = repo.attempt(FAILED_ATTEMPT)
    if not attempt:
        pytest.skip('Original failed attempt unavailable')
    assert repo.result(FAILED_ATTEMPT) is None
    # Failed confirmations are not persisted. Replay stored STT text unless already confirmed.
    text = attempt['confirmed_text'] or json.loads(attempt['transcription'])['text']
    before = repo.profile().completed_attempts
    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                body = {'text': text, 'confirmed': True}
                path = f'/api/attempts/{FAILED_ATTEMPT}/evaluate'
                result = await client.post(path, json=body)
                print('Live evaluator replay status:', result.status_code, flush=True)
                assert result.status_code == 200
                assert repo.profile().completed_attempts == before + 1
                assert repo.result(FAILED_ATTEMPT) == result.json()
                assert len([entry for entry in repo.history() if entry['id'] == FAILED_ATTEMPT]) == 1
                repeated = await client.post(path, json=body)
                assert repeated.status_code == 200 and repeated.json() == result.json()
                assert repo.profile().completed_attempts == before + 1
                print('Evaluation persisted on isolated copy; repeat is idempotent; original DB unchanged.', flush=True)
    asyncio.run(check())
