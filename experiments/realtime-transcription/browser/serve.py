"""Local helper for the browser test page: serves these files and mints a SHORT-LIVED client secret so the permanent
API key never reaches the browser. Binds to 127.0.0.1 only; put an HTTPS tunnel in front of it for an iPhone
(getUserMedia needs a secure context). Mints secrets only for transcription sessions; refuses everything else.

    OPENAI_API_KEY=... python serve.py [--port 8765]     (or --env-file ../../../.env; only OPENAI_API_KEY is read)
"""
import argparse
import hmac
import json
import os
import secrets
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
SESSION = {'session': {'type': 'transcription', 'audio': {'input': {'format': {'type': 'audio/pcm', 'rate': 24000},
           'transcription': {'model': 'gpt-live-transcribe', 'languages': ['en']}, 'turn_detection': None}}}}


TOKEN = secrets.token_urlsafe(24)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def do_POST(self):
        if self.path != '/secret':
            return self.send_error(404)
        # A tunnel makes this reachable from the internet: require the random token printed at startup.
        if not hmac.compare_digest(self.headers.get('X-Token', ''), TOKEN):
            return self.send_error(403)
        request = Request('https://api.openai.com/v1/realtime/client_secrets', data=json.dumps(SESSION).encode(), method='POST',
                          headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
        with urlopen(request, timeout=20) as response:
            body = json.load(response)
        payload = json.dumps({'value': body.get('value'), 'expires_at': body.get('expires_at')}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def log_message(self, *args):   # no request logging
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            name, _, value = line.partition('=')
            if name.strip() == 'OPENAI_API_KEY' and value.strip():
                os.environ['OPENAI_API_KEY'] = value.strip().strip('"\'')
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('Set OPENAI_API_KEY (or --env-file).')
    print(f'http://127.0.0.1:{args.port}/index.html#{TOKEN}  (tunnel over HTTPS for iPhone Safari; stop the server when done)')
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
