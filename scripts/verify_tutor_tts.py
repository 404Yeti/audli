"""Opt-in AUD-25 local authenticated audio test; strict paid-call ledger, no retries."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import uvicorn


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, output: Path):
        self.output = output
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.ledger = {'maximum_attempts': 6, 'maximum_characters': 600,
            'maximum_eleven_attempts': 2, 'maximum_eleven_estimated_usd': .02,
            'attempts': 0, 'characters': 0, 'eleven_attempts': 0, 'eleven_estimated_usd': 0}

    def save(self):
        temp=self.output/'budget.tmp'
        temp.write_text(json.dumps(self.ledger,indent=2)+'\n')
        temp.replace(self.output/'budget.json')

    async def handle_async_request(self, request):
        if request.method == 'POST':
            payload=json.loads(request.content)
            chars=len(payload.get('text',payload.get('input','')))
            eleven=request.url.host=='api.elevenlabs.io'
            l=self.ledger
            if l['attempts']>=6 or l['characters']+chars>600 or (eleven and
                (l['eleven_attempts']>=2 or l['eleven_estimated_usd']+chars*.04/1000>.02)):
                raise ValueError('Local synthesis budget exhausted')
            l['attempts']+=1;l['characters']+=chars
            if eleven:l['eleven_attempts']+=1;l['eleven_estimated_usd']+=chars*.04/1000
            self.save()  # Reserve before network, even for an ambiguous failure.
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def build(output: Path, env_file: Path):
    # Isolate the module-level app from any production persistence in local .env.
    os.environ.update(AUDLI_ENVIRONMENT='development',AUDLI_PERSISTENCE='sqlite',
        AUDLI_DATABASE_URL='',AUDLI_AUTH_MODE='local',AUDLI_LEARNER_ID='',
        AUDLI_DATA_DIR=str(output/'bootstrap'),AUDLI_TUTOR_TTS_PROVIDER='openai')
    from app.config import Settings
    from app.main import create_app
    from app.services.openai_provider import OpenAIProvider
    from app.services.tutor_tts import TutorTTSProvider, tts_measurements
    from fastapi.responses import HTMLResponse
    config=Settings(_env_file=(Path('.env'),env_file),environment='development',persistence='sqlite',
        database_url=None,learner_id=None,data_dir=output/'state',provider='openai',
        auth_mode='supabase',supabase_url='https://aud25-test.supabase.co',
        supabase_publishable_key='sb_publishable_local_fixture',allowed_origins='',
        tutor_tts_provider='elevenlabs')
    if not config.openai_api_key or not config.elevenlabs_api_key:
        raise ValueError('Both backend credentials required')
    transport=BudgetTransport(output);transport.save()
    base=OpenAIProvider(config)
    # No network has started on the replaced default SDK client.
    from openai import AsyncOpenAI
    asyncio.run(base.client.close())
    base.client=AsyncOpenAI(api_key=config.openai_api_key.get_secret_value(),max_retries=0,
        http_client=httpx.AsyncClient(transport=transport))
    router=TutorTTSProvider(base,config,httpx.AsyncClient(transport=transport))
    user=str(UUID('00000000-0000-4000-8000-000000000025'))
    def verify(request):
        return httpx.Response(200,json={'id':user,'email_confirmed_at':'2026-10-10','is_anonymous':False}) if request.headers.get('Authorization')=='Bearer local-aud25-fixture' else httpx.Response(401)
    app=create_app(config,router,auth_client=httpx.AsyncClient(transport=httpx.MockTransport(verify)))
    @app.middleware('http')
    async def measurements(request,call_next):
        rows=[];token=tts_measurements.set(rows)
        try:
            response=await call_next(request)
            response.headers['X-Aud25-Probe-TTS']=json.dumps(rows,separators=(',',':'))
            return response
        finally:tts_measurements.reset(token)
    @app.post('/__aud25/provider/{provider}')
    async def select(provider: str):
        if provider not in ('openai','elevenlabs'):raise ValueError('Invalid test provider')
        config.tutor_tts_provider=provider
        return {'provider':provider}
    @app.get('/__aud25')
    async def page():
        return HTMLResponse('<button>Enable audio</button><p>AUD-25 local playback probe</p>')
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',required=True)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    try:
        app=build(args.output,args.env_file)
        uvicorn.run(app,host='127.0.0.1',port=8025,log_level='warning',access_log=False)
    except Exception:
        raise SystemExit('Local probe setup failed; see non-secret budget ledger if created') from None
