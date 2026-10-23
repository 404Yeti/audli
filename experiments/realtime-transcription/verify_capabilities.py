"""Free/near-free capability probe, run BEFORE any paid test: do the documented models, session fields and the
browser client-secret flow actually work for this account? Sends no speech (a few hundred ms of silence at most).

    python verify_capabilities.py --env-file /path/.env --out ~/audli-realtime-experiment/capabilities.json
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corpus import load_key  # noqa: E402
from spend import Ledger  # noqa: E402
from streaming import URL, parse_event, session_update  # noqa: E402

DELAYS = ('minimal', 'low', 'medium', 'high', 'xhigh')


async def probe_session(key: str, update: dict, settle: float = 1.5, subprotocols=None, headers=True) -> dict:
    from websockets.asyncio.client import connect
    seen, errors = [], []
    kwargs = {'additional_headers': {'Authorization': f'Bearer {key}'}} if headers else {}
    if subprotocols:
        kwargs['subprotocols'] = subprotocols
    try:
        async with connect(URL, open_timeout=15, **kwargs) as socket:
            await socket.send(json.dumps(update))
            try:
                async with asyncio.timeout(settle):
                    async for message in socket:
                        event = parse_event(message)
                        seen.append(event['type'])
                        if event['kind'] == 'error':
                            errors.append(event['error_code'])
            except TimeoutError:
                pass
        return {'connected': True, 'events': sorted(set(seen)), 'errors': sorted(set(errors))}
    except Exception as error:  # type only: messages may echo request details
        return {'connected': False, 'failure': type(error).__name__}


async def main(env_file: Path, out: Path) -> dict:
    load_key(env_file)
    key = os.environ['OPENAI_API_KEY']
    ledger = Ledger(out.parent / 'ledger.json')
    ledger.reserve('capability-probe', 0.02)
    report: dict = {}
    import httpx
    async with httpx.AsyncClient(timeout=20) as http:
        models = (await http.get('https://api.openai.com/v1/models', headers={'Authorization': f'Bearer {key}'})).json().get('data', [])
        ids = {m['id'] for m in models}
        report['models_listed'] = {name: name in ids for name in ('gpt-live-transcribe', 'gpt-transcribe', 'gpt-4o-mini-transcribe', 'gpt-live-1')}
        report['live_transcribe_delays'] = {d: await probe_session(key, session_update('gpt-live-transcribe', delay=d)) for d in DELAYS}
        report['live_transcribe_hints'] = await probe_session(key, session_update(
            'gpt-live-transcribe', languages=('en',), prompt='A short spoken answer from a Vietnamese learner of English.', keywords=('rolled back', 'migration')))
        report['gpt_transcribe'] = await probe_session(key, session_update('gpt-transcribe'))
        report['invalid_delay_rejected'] = await probe_session(key, session_update('gpt-live-transcribe', delay='bogus'))
        secret_body = {'session': {'type': 'transcription', 'audio': {'input': {
            'format': {'type': 'audio/pcm', 'rate': 24000}, 'transcription': {'model': 'gpt-live-transcribe', 'languages': ['en']}, 'turn_detection': None}}}}
        response = await http.post('https://api.openai.com/v1/realtime/client_secrets', headers={'Authorization': f'Bearer {key}'}, json=secret_body)
        body = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
        secret = body.get('value') or (body.get('client_secret') or {}).get('value')
        report['client_secret'] = {'status': response.status_code, 'keys': sorted(body.keys()), 'has_value': bool(secret),
                                   'expires_present': 'expires_at' in body or 'expires_at' in (body.get('client_secret') or {})}
        if secret:
            # What a browser would do: it cannot set headers, so try the documented-for-browsers subprotocol form.
            report['browser_style_websocket'] = await probe_session(secret, session_update('gpt-live-transcribe', delay='low'),
                                                                    subprotocols=['realtime', f'openai-insecure-api-key.{secret}'], headers=False)
            report['ephemeral_secret_as_bearer'] = await probe_session(secret, session_update('gpt-live-transcribe', delay='low'))
    ledger.record('capability-probe', 0.005, 'sessions opened without audio; negligible, charged conservatively')
    out.write_text(json.dumps(report, indent=1))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path.home() / 'audli-realtime-experiment' / 'capabilities.json')
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(asyncio.run(main(args.env_file, args.out)), indent=1))
