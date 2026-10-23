"""Streaming-transcription client for the experiment: real-time replay of a recorded turn over the Realtime
transcription WebSocket, with client-side end-of-speech detection and an explicit commit.

Event and field names come from OpenAI's Realtime transcription guide (read 2026-10-23); anything the guide does not
document is observed and counted (``event_types``) rather than assumed. Results hold numbers and the transcript text
of synthetic/consented test audio only; callers decide whether to persist text.
"""
import asyncio
import base64
import json
from time import perf_counter

from vad import VadConfig, detect_end

URL = 'wss://api.openai.com/v1/realtime?intent=transcription'
CHUNK_MS = 100
DELTA = 'conversation.item.input_audio_transcription.delta'
COMPLETED = 'conversation.item.input_audio_transcription.completed'


def session_update(model: str, *, languages=('en',), prompt: str | None = None, keywords=None, delay: str | None = None) -> dict:
    transcription = {'model': model}
    if model == 'gpt-live-transcribe':
        # Hints documented for gpt-live-transcribe only; `languages` replaces the singular `language` field.
        if languages:
            transcription['languages'] = list(languages)
        if prompt:
            transcription['prompt'] = prompt
        if keywords:
            transcription['keywords'] = list(keywords)
        if delay:
            transcription['delay'] = delay
    return {'type': 'session.update', 'session': {'type': 'transcription', 'audio': {'input': {
        'format': {'type': 'audio/pcm', 'rate': 24000}, 'transcription': transcription, 'turn_detection': None}}}}


def parse_event(raw: str) -> dict:
    """Reduce a server message to {kind, item_id, text, languages, error_code}; unknown types keep only their name."""
    event = json.loads(raw)
    kind = event.get('type', '')
    parsed = {'type': kind, 'kind': 'other', 'item_id': event.get('item_id'), 'text': None, 'languages': None, 'error_code': None}
    if kind == DELTA:
        parsed.update(kind='delta', text=event.get('delta', ''))
    elif kind == COMPLETED:
        parsed.update(kind='completed', text=event.get('transcript', ''), languages=event.get('languages'))
    elif kind == 'error' or kind.endswith('.failed'):
        error = event.get('error') or {}
        parsed.update(kind='error', error_code=str(error.get('code') or error.get('type') or kind)[:64])
    return parsed


async def stream_turn(pcm: bytes, rate: int, *, api_key: str, model: str, vad_config: VadConfig, delay: str | None = None,
                      prompt: str | None = None, languages=('en',), keywords=None, url: str = URL, final_timeout: float = 20.0) -> dict:
    """Replay ``pcm`` (16-bit mono, 24 kHz) in real time until the detector ends the turn, commit, wait for the final text."""
    from websockets.asyncio.client import connect
    vad = detect_end(pcm, rate, vad_config)
    end_ms = vad['end_ms'] if vad['ended'] else len(pcm) * 1000 // (2 * rate)
    audio = pcm[:end_ms * rate // 1000 * 2]
    events: list[tuple[float, dict]] = []
    done = asyncio.Event()
    connect_started = perf_counter()
    async with connect(url, additional_headers={'Authorization': f'Bearer {api_key}'}, max_size=None, open_timeout=15) as socket:
        connect_ms = (perf_counter() - connect_started) * 1000
        t0 = 0.0

        async def receive():
            async for message in socket:
                parsed = parse_event(message)
                events.append(((perf_counter() - t0) * 1000, parsed))
                if parsed['kind'] == 'completed' and parsed['text'] is not None:
                    done.set()
                if parsed['kind'] == 'error':
                    done.set()
        await socket.send(json.dumps(session_update(model, languages=languages, prompt=prompt, keywords=keywords, delay=delay)))
        receiver = asyncio.create_task(receive())
        step = rate * CHUNK_MS // 1000 * 2
        t0 = perf_counter()
        for index, offset in enumerate(range(0, len(audio), step), 1):
            # A microphone cannot deliver audio before it was spoken: chunk i is available at (i x 100) ms.
            await asyncio.sleep(max(0.0, t0 + index * CHUNK_MS / 1000 - perf_counter()))
            await socket.send(json.dumps({'type': 'input_audio_buffer.append', 'audio': base64.b64encode(audio[offset:offset + step]).decode()}))
        commit_ms = (perf_counter() - t0) * 1000
        await socket.send(json.dumps({'type': 'input_audio_buffer.commit'}))
        try:
            await asyncio.wait_for(done.wait(), final_timeout)
            timed_out = False
        except TimeoutError:
            timed_out = True
        completed_ms = (perf_counter() - t0) * 1000
        receiver.cancel()
    deltas = [(t, e['text']) for t, e in events if e['kind'] == 'delta']
    finals = [(t, e) for t, e in events if e['kind'] == 'completed']
    errors = sorted({e['error_code'] for _, e in events if e['kind'] == 'error'})
    first_voice = vad['first_voice_ms']
    return {
        'model': model, 'vad': vad, 'streamed_seconds': round(len(audio) / 2 / rate, 3), 'connect_ms': round(connect_ms, 1),
        'timed_out': timed_out, 'errors': errors,
        'text': finals[0][1]['text'] if finals else '', 'languages': finals[0][1]['languages'] if finals else None,
        'partial_text': ''.join(text for _, text in deltas),
        'commit_ms': round(commit_ms, 1), 'final_latency_ms': round(finals[0][0] - commit_ms, 1) if finals else None,
        'first_partial_ms_after_speech_start': round(deltas[0][0] - first_voice, 1) if deltas and first_voice is not None else None,
        'partials_before_commit': sum(1 for t, _ in deltas if t < commit_ms), 'partials': len(deltas),
        'last_partial_to_final_ms': round(finals[0][0] - deltas[-1][0], 1) if deltas and finals else None,
        'event_types': {name: sum(1 for _, e in events if e['type'] == name) for name in sorted({e['type'] for _, e in events})},
    }
