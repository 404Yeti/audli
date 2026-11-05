"""Build one lesson's audio from its script: each sentence is synthesised separately, measured, trimmed and joined, so every transcript
cue is MEASURED on the final timeline rather than estimated (this is what keeps the transcript in sync with natural speech).

    sentences --(SpeechProvider, one voice per speaker)--> clips --decode--> PCM --trim edge silence--> measure --join with pauses-->
    encode MP3 (no metadata) --decode again--> verify duration --> BuiltAudio(bytes, cues, checksum)

Quality gates per sentence reject silent, truncated or runaway clips before anything is published. Nothing here talks to a database or a
store: publishing is seed.py's job, as a new immutable lesson version.
"""
import hashlib
import struct
import sys
from array import array
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.listening.domain import Cue, LessonContent, validate_alignment

RATE = 44100
LEAD_MS, PAUSE_MS, TAIL_MS, EDGE_MS = 300, 450, 400, 120
SECONDS_PER_WORD = (0.15, 1.1)      # a clip outside this pace is truncated, padded or garbled
MIN_PEAK = 1500                      # quieter than this is effectively silence
TOLERANCE_MS = 150                   # encoder delay and padding


class AudioQualityError(Exception):
    pass


@dataclass(frozen=True)
class BuiltAudio:
    data: bytes
    cues: list[Cue]
    duration_ms: int
    sha256: str
    voices: dict[str, str]

    def meta(self, provider: str, accent: str = 'american') -> dict:
        voice = ' + '.join(f'{s}={v}' for s, v in sorted(self.voices.items()))[:60]
        return {'accent': accent, 'voice': voice, 'provider': provider, 'content_type': 'audio/mpeg', 'file': 'audio.mp3', 'sha256': self.sha256,
                'size_bytes': len(self.data), 'duration_ms': self.duration_ms, 'cues': [c.model_dump() for c in self.cues]}


def _ffmpeg() -> str:
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


def _little_endian(pcm) -> bytes:
    if not isinstance(pcm, array):
        pcm = array('h', pcm)
    elif sys.byteorder == 'big':
        pcm = array('h', pcm)
    if sys.byteorder == 'big':
        pcm.byteswap()
    return pcm.tobytes()


def decode(data: bytes, rate: int = RATE):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'in.mp3'
        source.write_bytes(data)
        done = subprocess.run([_ffmpeg(), '-v', 'error', '-nostdin', '-i', str(source), '-vn', '-ac', '1', '-ar', str(rate), '-f', 's16le', 'pipe:1'],
                              capture_output=True, timeout=60)
    if done.returncode != 0 or not done.stdout:
        raise AudioQualityError('a clip could not be decoded')
    samples = array('h')                            # 2 bytes a sample, not a Python int object each: a two-minute lesson must fit beside the API in a 512 MB container
    samples.frombytes(done.stdout[: len(done.stdout) // 2 * 2])
    if sys.byteorder == 'big':
        samples.byteswap()
    return samples


def trim(samples):
    """Keep a short, consistent margin of silence at both ends so pacing does not depend on the provider's padding."""
    first = next((i for i, s in enumerate(samples) if abs(s) > 400), None)
    if first is None:
        raise AudioQualityError('a clip is silent')
    last = len(samples) - 1 - next(i for i, s in enumerate(reversed(samples)) if abs(s) > 400)
    pad = RATE * EDGE_MS // 1000
    return samples[max(0, first - pad): min(len(samples), last + pad)]


def check_clip(text: str, samples) -> None:
    seconds, words = len(samples) / RATE, max(1, len(text.split()))
    low, high = SECONDS_PER_WORD
    if max(abs(s) for s in samples) < MIN_PEAK:
        raise AudioQualityError(f'a clip is almost silent: {text[:40]!r}')
    if seconds < max(0.4, low * words) or seconds > high * words + 1.0:
        raise AudioQualityError(f'a clip has an implausible length ({seconds:.1f}s for {words} words): {text[:40]!r}')


def encode(pcm) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        raw, out = Path(directory) / 'a.raw', Path(directory) / 'a.mp3'
        raw.write_bytes(_little_endian(pcm))
        subprocess.run([_ffmpeg(), '-v', 'error', '-y', '-f', 's16le', '-ar', str(RATE), '-ac', '1', '-i', str(raw), '-b:a', '96k', '-map_metadata', '-1',
                        '-id3v2_version', '0', '-write_id3v1', '0', str(out)], check=True, timeout=120)
        return out.read_bytes()


def build_audio(content: LessonContent, provider, voices: dict[str, str]) -> BuiltAudio:
    missing = {s.id for s in content.speakers} - set(voices)
    if missing:
        raise ValueError(f'No voice chosen for: {", ".join(sorted(missing))}')
    silence = lambda ms: array("h", bytes(2 * max(0, RATE * ms // 1000)))
    pcm, cues = silence(LEAD_MS), []
    for sentence in content.sentences:
        clip = trim(decode(provider.synthesize(sentence.text, voices[sentence.speaker])))
        check_clip(sentence.text, clip)
        start = len(pcm) * 1000 // RATE
        pcm += clip
        cues.append(Cue(index=sentence.index, start_ms=start, end_ms=len(pcm) * 1000 // RATE))
        pcm += silence(PAUSE_MS)
    pcm += silence(TAIL_MS - PAUSE_MS)
    data = encode(pcm)
    duration_ms = len(pcm) * 1000 // RATE
    heard_ms = len(decode(data)) * 1000 // RATE
    if abs(heard_ms - duration_ms) > TOLERANCE_MS:
        raise AudioQualityError(f'the encoded audio is {heard_ms} ms but the cues were measured on {duration_ms} ms')
    validate_alignment(cues, len(content.sentences), min(duration_ms, heard_ms))
    return BuiltAudio(data=data, cues=cues, duration_ms=min(duration_ms, heard_ms), sha256=hashlib.sha256(data).hexdigest(), voices=dict(voices))
