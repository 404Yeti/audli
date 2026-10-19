"""AUD-70: observe (never limit) concurrent ElevenLabs synthesis. Creator plan, Flash/Turbo models: 10 concurrent requests.

Monitoring queries for the Render logs:
  rejected by concurrency/rate limit : `Tutor TTS` lines with "http_status": 429
  fallback frequency                 : `Tutor TTS` lines with "event": "fallback"
  concurrency                        : per-call "inflight_at_start" and the `tts.elevenlabs.inflight` gauge in `Latency summary`
"""
import asyncio
import json

import httpx
import pytest

from app.audio import sanitize_generated_audio
from app.services import tutor_tts
from app.services.tutor_tts import TutorTTSProvider, tts_measurements
from app.telemetry import aggregator
from conftest import FakeProvider
from test_tutor_tts import mp3, settings  # noqa: F401  (fixtures/helpers)


def router(mp3, handler, **values):
    base = FakeProvider()
    async def speak(text, rate=.9): return mp3
    base.speak = speak
    return TutorTTSProvider(base, settings(**values), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_concurrent_calls_are_observed_and_the_counter_returns_to_zero(mp3):
    aggregator.reset()
    metrics = []
    async def handler(request):
        await asyncio.sleep(.15)
        return httpx.Response(200, content=mp3)
    async def run():
        r = router(mp3, handler, latency_aggregates=True)
        token = tts_measurements.set(metrics)
        try:
            await asyncio.gather(*(r.speak(f'cue number {i}') for i in range(6)))
        finally:
            tts_measurements.reset(token); await r.close()
    asyncio.run(run())
    starts = sorted(m['inflight_at_start'] for m in metrics if m.get('provider') == 'elevenlabs')
    assert starts == [1, 2, 3, 4, 5, 6]                      # six overlapping calls observed as 1..6 in flight
    assert tutor_tts._elevenlabs_inflight == 0
    gauge = aggregator.snapshot()['gauges']['tts.elevenlabs.inflight']
    assert gauge == {str(i): 1 for i in range(1, 7)}


def test_429_is_visible_counts_inflight_and_falls_back_exactly_once(mp3):
    aggregator.reset()
    metrics = []
    async def handler(request): return httpx.Response(429, json={'detail': {'status': 'too_many_concurrent_requests'}})
    async def run():
        r = router(mp3, handler, latency_aggregates=True)
        token = tts_measurements.set(metrics)
        try:
            return await r.speak('a cue that is rate limited'), r.last_fallback
        finally:
            tts_measurements.reset(token); await r.close()
    audio, fallback = asyncio.run(run())
    assert audio and fallback is True                        # learner still hears a cue (OpenAI voice)
    rejected = [m for m in metrics if m.get('provider') == 'elevenlabs']
    assert rejected and rejected[0]['http_status'] == 429 and rejected[0]['outcome'] == 'failure'
    assert any(m.get('event') == 'fallback' for m in metrics)
    assert tutor_tts._elevenlabs_inflight == 0               # a failed call still releases its slot


def test_cancelled_call_releases_its_slot(mp3):
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, content=mp3)
    async def run():
        r = router(mp3, handler)
        task = asyncio.create_task(r.speak('cancel me'))
        await asyncio.sleep(.1)
        assert tutor_tts._elevenlabs_inflight == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await r.close()
    asyncio.run(run())
    assert tutor_tts._elevenlabs_inflight == 0


def test_gauge_is_off_without_the_flag_but_the_log_field_remains(mp3):
    aggregator.reset()
    metrics = []
    async def handler(request): return httpx.Response(200, content=mp3)
    async def run():
        r = router(mp3, handler)                              # latency_aggregates defaults to False
        token = tts_measurements.set(metrics)
        try:
            await r.speak('a normal cue')
        finally:
            tts_measurements.reset(token); await r.close()
    asyncio.run(run())
    assert aggregator.snapshot()['gauges'] == {}
    assert [m['inflight_at_start'] for m in metrics if m.get('provider') == 'elevenlabs'] == [1]
    assert 'text' not in json.dumps(metrics) and 'rate limited' not in json.dumps(metrics)   # numeric/enum fields only


def test_gauge_names_and_levels_are_bounded():
    aggregator.reset()
    for i in range(100):
        aggregator.record_gauge(f'g.{i}', i)
    aggregator.record_gauge('free text here', 1)
    gauges = aggregator.snapshot()['gauges']
    assert len(gauges) <= 17 and 'other' in gauges
    assert all(int(level) <= 32 for levels in gauges.values() for level in levels)
