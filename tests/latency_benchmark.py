"""Synthetic prompt replay microbenchmark; never a live provider/end-to-end claim.
Run: .venv/bin/python tests/latency_benchmark.py
The fixed 200ms provider delay and generated WAV isolate cache plumbing.
"""
import asyncio
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import conftest  # noqa: E402 -- isolates environment before app import
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import ASGIClient, FakeProvider  # noqa: E402


def main():
    provider = FakeProvider()
    calls = 0
    async def speech(text, speech_rate=.9):
        nonlocal calls
        calls += 1
        await asyncio.sleep(.2)
        return await provider.speech(None)
    provider.speak = speech
    with tempfile.TemporaryDirectory() as directory:
        client = ASGIClient(create_app(Settings(data_dir=directory, provider='demo', _env_file=None), provider), provider)
        state = client.post('/api/onboarding/start', json={'revision':0}).json()
        samples = []
        for _ in range(12):
            started = perf_counter()
            response = client.post('/api/onboarding/audio', json={'revision':state['revision']})
            assert response.status_code == 200, response.status_code
            samples.append((perf_counter() - started) * 1000)
        print(json.dumps({'workload':'synthetic repeated onboarding prompt; complete HTTP body, no browser',
            'n':len(samples), 'p50_ms':round(statistics.median(samples), 1),
            'p90_ms':round(sorted(samples)[math.ceil(.9 * len(samples)) - 1], 1),
            'tts_calls':calls, 'samples_ms':[round(value, 1) for value in samples]}))


if __name__ == '__main__':
    main()
