import asyncio
import httpx
import pytest
from scripts.verify_tutor_tts import BudgetTransport


def test_live_probe_reserves_before_network_and_refuses_excess(tmp_path):
    calls=[]
    async def handler(request):
        calls.append(request)
        assert transport.ledger['attempts']==len(calls)
        return httpx.Response(200)
    transport=BudgetTransport(tmp_path)
    asyncio.run(transport.inner.aclose())
    transport.inner=httpx.MockTransport(handler)
    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(2):
                await client.post('https://api.elevenlabs.io/v1/text-to-speech/test/stream',json={'text':'x'*100})
            with pytest.raises(ValueError,match='budget exhausted'):
                await client.post('https://api.elevenlabs.io/v1/text-to-speech/test/stream',json={'text':'x'})
            with pytest.raises(ValueError,match='budget exhausted'):
                await client.post('https://api.openai.com/v1/audio/speech',json={'input':'x'*401})
    asyncio.run(run())
    assert len(calls)==2 and transport.ledger['attempts']==2
    assert transport.ledger['characters']==200
