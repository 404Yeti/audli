import asyncio
import math
import struct
import tempfile
from imageio_ffmpeg import get_ffmpeg_exe
from pathlib import Path
from fastapi import HTTPException, UploadFile
from app.config import Settings

MIMES = {'audio/webm': 'webm', 'video/webm': 'webm', 'audio/mp4': 'mp4', 'audio/x-m4a': 'mp4',
         'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/ogg': 'ogg'}


def signature_matches(data: bytes, kind: str):
    return {'webm': data.startswith(b'\x1aE\xdf\xa3'),
            'mp4': len(data) > 12 and data[4:8] == b'ftyp',
            'mp3': data.startswith(b'ID3') or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224),
            'wav': data.startswith(b'RIFF') and data[8:12] == b'WAVE',
            'ogg': data.startswith(b'OggS')}.get(kind, False)


async def validate_upload(upload: UploadFile, settings: Settings):
    kind = MIMES.get((upload.content_type or '').split(';')[0].lower())
    if not kind:
        raise HTTPException(415, 'Use WebM, MP4/M4A, MP3, Ogg or WAV audio.')
    chunks, size = [], 0
    while chunk := await upload.read(64 * 1024):
        size += len(chunk)
        if size > settings.max_audio_bytes:
            raise HTTPException(413, 'Recording exceeds 12 MB.')
        chunks.append(chunk)
    data = b''.join(chunks)
    if size < 256 or not signature_matches(data, kind):
        raise HTTPException(422, 'The recording is empty or is not a supported audio file.')
    # Decode under a hard duration cap. Do not trust browser filenames or duration metadata.
    with tempfile.TemporaryDirectory(prefix='audli-audio-') as directory:
        path = Path(directory) / f'input.{kind}'
        path.write_bytes(data)
        try:
            process = await asyncio.create_subprocess_exec(get_ffmpeg_exe(), '-v', 'error', '-nostdin',
                '-i', str(path), '-t', str(settings.max_recording_seconds + 1), '-vn', '-ac', '1',
                '-ar', '8000', '-f', 's16le', 'pipe:1', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            raise HTTPException(503, 'Audio validation requires ffmpeg. Install it and restart.')
        try:
            pcm, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise HTTPException(422, 'Recording could not be decoded.')
        seconds = len(pcm) / 16000
        if process.returncode != 0 or seconds < 2 or seconds > settings.max_recording_seconds:
            raise HTTPException(422, 'Record between 2 and 120 seconds of speech.')
        samples = struct.unpack(f'<{len(pcm)//2}h', pcm)
        rms = math.sqrt(sum(s*s for s in samples) / len(samples))
        if rms < 30:
            raise HTTPException(422, 'The recording seems silent. Check your microphone and try again.')
    return data, f'response.{kind}'


async def sanitize_generated_audio(data: bytes, kind: str) -> bytes:
    """Decode generated speech and discard tags that could carry the hidden script."""
    with tempfile.TemporaryDirectory(prefix='audli-speech-') as directory:
        source = Path(directory) / 'source'
        destination = Path(directory) / f'clip.{kind}'
        source.write_bytes(data)
        args = [get_ffmpeg_exe(), '-v', 'error', '-nostdin', '-i', str(source), '-vn',
                '-map_metadata', '-1', '-t', '150']
        if kind == 'mp3':
            args += ['-codec:a', 'libmp3lame', '-id3v2_version', '0', '-write_id3v1', '0']
        args += [str(destination)]
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(), timeout=30)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ValueError('Speech sanitization timed out')
        if process.returncode != 0 or not destination.exists():
            raise ValueError('Generated speech is not decodable audio')
        return destination.read_bytes()
