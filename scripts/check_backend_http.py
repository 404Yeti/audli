"""Start the existing backend and check real localhost HTTP, without any AI calls."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx


def main():
    with tempfile.TemporaryDirectory(prefix='audli-http-check-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'AUDLI_DATA_DIR': str(Path(directory) / 'data')}
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Backend exited before accepting requests.')
                    try:
                        health = client.get('/api/health')
                        break
                    except httpx.ConnectError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Backend did not become ready within 10 seconds.')
                assert health.status_code == 200 and health.json()['status'] == 'ok'
                profile = client.get('/api/profile')
                assert profile.status_code == 200
                assert 'script' not in profile.text
                current = client.get('/api/exercises/current')
                assert current.status_code == 200 and current.json() is None
                assert profile.headers['cache-control'] == 'no-store'
                print('PASS: Uvicorn startup and real HTTP health/profile/current-exercise requests.')
                print('No OpenAI calls, audio playback, recording or frontend communication tested.')
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    try:
        main()
    except PermissionError as exc:
        raise SystemExit(f'BLOCKED: localhost socket access is unavailable ({exc}).') from None
