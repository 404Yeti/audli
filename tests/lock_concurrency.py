"""Controlled two-learner measurement of the shared ``asyncio.Lock`` (AUD-68 Phase 0).

Providers are fakes with a fixed ``delay`` so the result isolates application locking from provider
variance. Numbers describe this harness, not production latency.
"""
import asyncio
import tempfile
from pathlib import Path
from time import perf_counter

import httpx

from app.config import Settings
from app.main import create_app
from app.models import Transcription
from app.telemetry import aggregator
from conftest import FakeProvider
from test_auth import USER_A, USER_B, auth_response, settings_for
from test_persistence import repository_factory as _unused  # noqa: F401  (fixture module import only)

WAV = None


def wav_bytes():
    global WAV
    if WAV is None:
        import io, math, struct, wave
        out = io.BytesIO()
        with wave.open(out, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
        WAV = out.getvalue()
    return WAV


class SlowProvider(FakeProvider):
    delay = 0.4

    async def transcribe(self, audio, filename):
        await asyncio.sleep(self.delay)
        return Transcription(text='The team rolled back the update after the migration removed a field.', confidence=.99, uncertainty=[])


async def measure(repository, delay=0.4):
    """Return wall-clock timings (seconds) for two learners hitting provider-bound routes."""
    provider = SlowProvider(); provider.delay = delay
    async def speak(text, speech_rate=.9):
        return await provider.speech(None)
    provider.speak = speak
    tmp = Path(tempfile.mkdtemp(prefix='audli-lock-'))
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp, latency_aggregates=True), provider, repository=repository, auth_client=auth)
    aggregator.reset()
    tokens = {'a': 'valid-a', 'b': 'valid-b'}
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        def head(who): return {'Authorization': 'Bearer ' + tokens[who]}
        for who, name in (('a', 'Alice'), ('b', 'Bob')):
            assert (await client.put('/api/profile', json={'name': name, 'goal': 'Meetings'}, headers=head(who))).status_code == 200
        exercises = {}
        for who in tokens:
            response = await client.post('/api/exercises', headers=head(who)); assert response.status_code == 200, response.text
            exercises[who] = response.json()['id']
            assert (await client.post(f'/api/exercises/{exercises[who]}/conversation/listened', headers=head(who))).status_code == 200

        async def upload(who):
            started = perf_counter()
            response = await client.post(f'/api/exercises/{exercises[who]}/attempts', headers=head(who),
                                         files={'audio': ('a.wav', wav_bytes(), 'audio/wav')})
            assert response.status_code == 200, response.text
            return perf_counter() - started

        # 1. Baseline: one learner alone, after a warm-up so first-request costs do not skew it.
        await upload('a')
        solo = await upload('a')
        # Fresh exercises are needed for repeat uploads; attempts are idempotent per exercise so reuse is fine.
        # 2. Two learners at the same instant.
        started = perf_counter()
        a, b = await asyncio.gather(upload('a'), upload('b'))
        both_wall = perf_counter() - started

        # 3. Head-of-line blocking: B asks for cheap things while A's STT is in flight.
        async def timed(method, path, who):
            started = perf_counter()
            response = await client.request(method, path, headers=head(who))
            return perf_counter() - started, response.status_code
        slow = asyncio.create_task(upload('a'))
        await asyncio.sleep(delay * 0.2)  # let A take the lock
        unlocked = await timed('GET', '/api/profile', 'b')                      # no lock on this route
        locked = await timed('POST', f'/api/exercises/{exercises["b"]}/conversation/listened', 'b')  # takes the lock
        await slow
    await auth.aclose()
    snapshot = aggregator.snapshot()
    app.state.repository.close()
    return {'provider_delay_s': delay, 'solo_upload_s': round(solo, 3),
            'two_learners_wall_s': round(both_wall, 3), 'two_learners_each_s': [round(a, 3), round(b, 3)],
            'serialization_ratio': round(both_wall / solo, 2),
            'b_unlocked_profile_read_s': round(unlocked[0], 3), 'b_locked_listened_s': round(locked[0], 3),
            'lock_queue_depth_at_arrival': snapshot['lock_queue_depth_at_arrival'],
            'turn_upload_lock_wait': snapshot['stages'].get('turn.upload.lock_wait'),
            'turn_upload_lock_hold': snapshot['stages'].get('turn.upload.lock_hold')}
