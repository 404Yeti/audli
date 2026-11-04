"""Build the checked-in seed-lesson audio locally. No network, no paid provider.

Each sentence is synthesised on its own with the open-source eNSpeak/espeak-ng engine (two different voices for the two
speakers) and joined with fixed pauses, so every sentence's start/end time is *measured*, not estimated. The result is a clearly
robotic development fixture, recorded as provider 'fixture'; it is not a production voice and not a quality benchmark.

Needs the espeak-ng command line on PATH (or ESPEAK_NG=/path/to/espeak-ng; dev-only, never a runtime dependency). Writes
audio.mp3 and audio.json next to lesson.json.
    .venv/bin/python -m scripts.build_listening_fixture
"""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

from app.listening.domain import LessonContent

FIXTURE = Path(__file__).resolve().parents[1] / 'app' / 'listening' / 'fixtures' / 'restaurant_b1'
VOICES = {'sam': ('en-us', 135, 45), 'maria': ('en-us+f3', 140, 60)}   # (voice, words per minute, pitch)
PAUSE_MS, LEAD_MS, TAIL_MS = 450, 300, 400


def synthesise(text: str, voice: str, wpm: int, pitch: int, directory: Path) -> tuple[list[int], int]:
    """One sentence through the espeak-ng command line -> (16-bit mono samples, sample rate)."""
    target = directory / 'sentence.wav'
    subprocess.run([os.environ.get('ESPEAK_NG', 'espeak-ng'), '-v', voice, '-s', str(wpm), '-p', str(pitch), '-w', str(target), '--', text],
                   check=True, capture_output=True)
    with wave.open(str(target), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2:
            raise RuntimeError('Unexpected espeak-ng output format')
        frames = clip.readframes(clip.getnframes())
        return list(struct.unpack(f'<{len(frames) // 2}h', frames)), clip.getframerate()


def main() -> None:
    content = LessonContent.model_validate_json((FIXTURE / 'lesson.json').read_text())
    scratch = tempfile.TemporaryDirectory()
    rate = None
    pcm, cues = [], []
    for sentence in content.sentences:
        voice, wpm, pitch = VOICES[sentence.speaker]
        clip, clip_rate = synthesise(sentence.text, voice, wpm, pitch, Path(scratch.name))
        if rate is None:
            rate = clip_rate
            silence = lambda ms: [0] * int(rate * ms / 1000)
            pcm = silence(LEAD_MS)
        elif clip_rate != rate:
            raise RuntimeError('Inconsistent sample rates')
        if len(clip) < rate // 4:
            raise RuntimeError(f'Sentence {sentence.index} produced no usable audio')
        start = len(pcm) * 1000 // rate
        pcm += clip
        cues.append({'index': sentence.index, 'start_ms': start, 'end_ms': len(pcm) * 1000 // rate})
        pcm += silence(PAUSE_MS)
    pcm += silence(TAIL_MS - PAUSE_MS)
    with tempfile.TemporaryDirectory() as directory:
        wav_path, mp3_path = Path(directory) / 'a.wav', Path(directory) / 'a.mp3'
        with wave.open(str(wav_path), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(struct.pack(f'<{len(pcm)}h', *pcm))
        subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-y', '-i', str(wav_path), '-ac', '1', '-ar', '22050', '-b:a', '64k',
                        '-map_metadata', '-1', '-id3v2_version', '0', '-write_id3v1', '0', str(mp3_path)], check=True)
        audio = mp3_path.read_bytes()
    duration_ms = len(pcm) * 1000 // rate
    (FIXTURE / 'audio.mp3').write_bytes(audio)
    (FIXTURE / 'audio.json').write_text(json.dumps({
        'accent': 'american', 'voice': 'espeak-ng en-us (development fixture)', 'provider': 'fixture',
        'content_type': 'audio/mpeg', 'file': 'audio.mp3', 'sha256': hashlib.sha256(audio).hexdigest(), 'size_bytes': len(audio),
        'duration_ms': duration_ms, 'cues': cues}, indent=2) + '\n')
    print(f'{len(content.sentences)} sentences, {duration_ms / 1000:.1f}s, {len(audio) / 1024:.0f} KiB')


if __name__ == '__main__':
    sys.exit(main())
