"""Validation of a learner's spoken answer before anything is stored: size, declared type, file signature, decodability, duration
and silence. Only the audio helpers shared with 1.x (MIME table and signature check) are reused; nothing from the conversation engine."""
import math
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.audio import MIMES, signature_matches

MAX_BYTES = 5 * 1024 * 1024
MIN_SECONDS = 2.0
MAX_SECONDS = 60.0
CANONICAL = {'webm': 'audio/webm', 'mp4': 'audio/mp4', 'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg'}


class RecordingRejected(Exception):
    """The message is safe to show the learner."""
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Recording:
    kind: str
    content_type: str
    seconds: float


def validate_recording(data: bytes, declared_type: str | None) -> Recording:
    kind = MIMES.get((declared_type or '').split(';')[0].strip().lower())
    if not kind:
        raise RecordingRejected('Use WebM, MP4/M4A, MP3, Ogg or WAV audio.', 415)
    if len(data) > MAX_BYTES:
        raise RecordingRejected('That recording is too large. Keep your answer under a minute.', 413)
    if len(data) < 256 or not signature_matches(data, kind):
        raise RecordingRejected('The recording is empty or is not a supported audio file.')
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        raise RecordingRejected('Recording is temporarily unavailable.', 503) from None
    with tempfile.TemporaryDirectory(prefix='audli-recording-') as directory:
        path = Path(directory) / f'input.{kind}'
        path.write_bytes(data)
        try:
            done = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(path), '-t', str(MAX_SECONDS + 1), '-vn', '-ac', '1',
                                   '-ar', '8000', '-f', 's16le', 'pipe:1'], capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            raise RecordingRejected('The recording could not be read.') from None
    seconds = len(done.stdout) / 16000
    if done.returncode != 0 or seconds < MIN_SECONDS or seconds > MAX_SECONDS:
        raise RecordingRejected('Record between 2 and 60 seconds.')
    samples = struct.unpack(f'<{len(done.stdout) // 2}h', done.stdout)
    if math.sqrt(sum(s * s for s in samples) / len(samples)) < 30:
        raise RecordingRejected('The recording seems silent. Check your microphone and try again.')
    return Recording(kind=kind, content_type=CANONICAL[kind], seconds=round(seconds, 1))
