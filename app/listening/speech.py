"""Text-to-speech providers for lesson audio. Used only by the offline audio build (scripts/build_listening_audio.py), never at request time.

The ElevenLabs request mirrors the one 1.x already uses in production (same endpoint, header, output format and voice settings), so the
shape is known to work with this account's key. It has NOT been run from this code: no paid call has been made. A hard character budget,
set explicitly by whoever approves the spend, is enforced before every request.
"""
import hashlib
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Protocol


class SpeechTemporary(Exception):
    """Timeouts, rate limits, 5xx: retry later."""


class SpeechPermanent(Exception):
    """Refused (auth, unknown voice, bad request): do not retry."""


class SpeechBudgetExceeded(Exception):
    pass


class SpeechProvider(Protocol):
    name: str

    def synthesize(self, text: str, voice: str) -> bytes: ...      # MP3 bytes


class SpeechBudget:
    """Characters the owner approved for one build. Reserved before each request; a request that would pass the cap is never sent."""
    def __init__(self, approved_chars: int):
        if approved_chars <= 0:
            raise ValueError('A speech budget must be a positive number of characters')
        self.approved, self.used = approved_chars, 0

    def reserve(self, text: str) -> None:
        if self.used + len(text) > self.approved:
            raise SpeechBudgetExceeded(f'{len(text)} more characters would pass the approved {self.approved} ({self.used} used)')
        self.used += len(text)


class FakeSpeech:
    """Deterministic, offline stand-in: a short tone whose length follows the text and whose pitch follows the voice. Tests and local runs only."""
    name = 'fake'

    def __init__(self, remaining: int | None = 1_000_000):
        self.calls, self.remaining = [], remaining

    def remaining_credits(self) -> int | None:
        return self.remaining

    def synthesize(self, text: str, voice: str) -> bytes:
        from imageio_ffmpeg import get_ffmpeg_exe
        self.calls.append((text, voice))
        seconds = max(0.8, 0.22 * len(text.split()) + 0.5)
        pitch = 180 + int(hashlib.sha256(voice.encode()).hexdigest()[:4], 16) % 260
        rate, count = 22050, int(22050 * seconds)
        samples = [int(9000 * math.sin(2 * math.pi * pitch * i / rate) * min(1, i / 400, (count - i) / 400)) for i in range(count)]
        with tempfile.TemporaryDirectory() as directory:
            wav, mp3 = Path(directory) / 'a.wav', Path(directory) / 'a.mp3'
            with wave.open(str(wav), 'wb') as out:
                out.setnchannels(1), out.setsampwidth(2), out.setframerate(rate), out.writeframes(struct.pack(f'<{count}h', *samples))
            subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-y', '-i', str(wav), '-b:a', '64k', str(mp3)], check=True)
            return mp3.read_bytes()


class ElevenLabsSpeech:
    name = 'elevenlabs'

    def __init__(self, api_key: str, model: str = 'eleven_multilingual_v2', budget: SpeechBudget | None = None, client=None, timeout: float = 60,
                 stability: float = 0.5, similarity_boost: float = 0.75, style: float = 0.0):
        if not api_key:
            raise ValueError('An ElevenLabs API key is required')
        if budget is None:
            raise ValueError('A speech budget is required: refusing to build a provider that could spend without a cap')
        self.api_key, self.model, self.budget, self.client, self.timeout = api_key, model, budget, client, timeout
        self.settings = {'stability': stability, 'similarity_boost': similarity_boost, 'style': style, 'use_speaker_boost': True}
        self.last_character_cost = None

    def remaining_credits(self) -> int | None:
        """Credits left on the account's plan this month (limit minus used), straight from the provider; None if it cannot be read (callers then refuse to start)."""
        import httpx
        try:
            response = (self.client or httpx).get('https://api.elevenlabs.io/v1/user/subscription', headers={'xi-api-key': self.api_key}, timeout=15, follow_redirects=False)
            data = response.json()
            return int(data['character_limit']) - int(data['character_count']) if response.status_code == 200 else None
        except Exception:
            return None

    def synthesize(self, text: str, voice: str) -> bytes:
        import httpx
        if not text.strip() or len(text) > 600:
            raise ValueError('One sentence at a time: 1-600 characters')
        self.budget.reserve(text)
        try:
            response = (self.client or httpx).post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice}', headers={'xi-api-key': self.api_key},
                params={'output_format': 'mp3_44100_128'}, timeout=self.timeout, follow_redirects=False,
                json={'text': text, 'model_id': self.model, 'voice_settings': self.settings})
        except httpx.TransportError:
            raise SpeechTemporary('network') from None
        status = response.status_code
        if status == 429 or status >= 500 or status in (408, 409):
            raise SpeechTemporary(f'status {status}')
        if status != 200:
            raise SpeechPermanent(f'status {status}')
        cost = response.headers.get('character-cost')
        self.last_character_cost = float(cost) if cost and cost.replace('.', '', 1).isdigit() else None
        data = response.content
        if len(data) < 256 or not (data.startswith(b'ID3') or (data[0] == 255 and data[1] & 224 == 224)):
            raise SpeechPermanent('not an MP3')
        return data
