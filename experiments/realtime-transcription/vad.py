"""Client-side end-of-speech detection, ported from web/lib/hands-free.ts `turnDetector` (RMS poll every 50 ms).

Same rules as production: a frame is "voiced" at RMS >= 0.015; the turn ends after 1.8 s of silence once at least
300 ms of voice and 2 s of recording exist; 15 s without 300 ms of voice, or 119 s total, also ends it.
The window is configurable so the experiment can test shorter and pause-aware variants.
"""
from dataclasses import dataclass
import math
import struct

POLL_MS = 50
THRESHOLD = 0.015


@dataclass(frozen=True)
class VadConfig:
    name: str
    window_ms: int = 1800
    adaptive: bool = False        # pause-aware: wait at least 1.25 x the longest pause heard so far in this turn
    adaptive_cap_ms: int = 2500
    min_total_ms: int = 2000      # production never ends a turn before 2 s of recording
    min_voiced_ms: int = 300


PRODUCTION = VadConfig('production-1800', 1800)
SHORT = VadConfig('short-1200', 1200)
ADAPTIVE = VadConfig('adaptive-1200', 1200, adaptive=True)


def frame_rms(frame: bytes) -> float:
    count = len(frame) // 2
    if not count:
        return 0.0
    samples = struct.unpack(f'<{count}h', frame[:count * 2])
    return math.sqrt(sum(s * s for s in samples) / count) / 32768.0


def frames(pcm: bytes, rate: int):
    """Yield (end_time_ms, rms) for consecutive 50 ms frames of 16-bit mono PCM."""
    size = rate * POLL_MS // 1000 * 2
    for index in range(0, len(pcm) - size + 1, size):
        yield (index // size + 1) * POLL_MS, frame_rms(pcm[index:index + size])


def detect_end(pcm: bytes, rate: int, config: VadConfig = PRODUCTION) -> dict:
    """Return when the detector would end the turn, plus what it saw. Times are ms from the start of the audio."""
    voiced = last_voice = previous = 0
    first_voice = None
    longest_pause = pause_start = 0
    in_pause = False
    for now, rms in frames(pcm, rate):
        active = rms >= THRESHOLD
        if active:
            if first_voice is None:
                first_voice = now
            if in_pause:
                longest_pause = max(longest_pause, now - pause_start)
                in_pause = False
            voiced += min(100, now - previous)
            last_voice = now
        elif first_voice is not None and not in_pause:
            in_pause, pause_start = True, last_voice
        previous = now
        window = config.window_ms
        if config.adaptive:
            window = min(config.adaptive_cap_ms, max(window, int(1.25 * longest_pause)))
        silent_for = now - last_voice
        ended = (now >= 119000
                 or (voiced >= config.min_voiced_ms and now >= config.min_total_ms and silent_for >= window)
                 or (voiced < config.min_voiced_ms and now >= 15000))
        if ended:
            return {'ended': True, 'end_ms': now, 'last_voice_ms': last_voice, 'first_voice_ms': first_voice,
                    'voiced_ms': voiced, 'window_ms': window, 'longest_pause_ms': longest_pause}
    return {'ended': False, 'end_ms': None, 'last_voice_ms': last_voice, 'first_voice_ms': first_voice,
            'voiced_ms': voiced, 'window_ms': config.window_ms, 'longest_pause_ms': longest_pause}
