import asyncio
import io
import wave
import subprocess
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from imageio_ffmpeg import get_ffmpeg_exe
from app.audio import sanitize_generated_audio, validate_upload
from app.config import Settings


def upload(data,mime='audio/wav'):
    return UploadFile(filename='answer',file=io.BytesIO(data),headers=Headers({'content-type':mime}))


def test_metadata_is_removed(tmp_path,audio_bytes):
    source=tmp_path/'original.wav'; source.write_bytes(audio_bytes)
    tagged=tmp_path/'tagged.mp3'
    secret='HIDDEN SCRIPT MUST NEVER REACH METADATA'
    subprocess.run([get_ffmpeg_exe(),'-v','error','-i',str(source),'-metadata','comment='+secret,str(tagged)],check=True)
    assert secret.encode() in tagged.read_bytes()
    sanitized=asyncio.run(sanitize_generated_audio(tagged.read_bytes(),'mp3'))
    assert secret.encode() not in sanitized
    assert b'ID3' not in sanitized[:10]


def test_silent_upload_rejected():
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\0'*48000)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(validate_upload(upload(output.getvalue()),Settings(_env_file=None)))
    assert exc.value.status_code==422


def test_size_limit(audio_bytes):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(validate_upload(upload(audio_bytes),Settings(max_audio_bytes=1000,_env_file=None)))
    assert exc.value.status_code==413


def test_valid_signature_but_invalid_audio_rejected():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(validate_upload(upload(b'RIFFxxxxWAVE'+b'garbage'*100),Settings(_env_file=None)))
    assert exc.value.status_code==422


# AUD-82: strip-only sanitization for tutor speech.
def _tagged_mp3(tmp_path, audio_bytes, secret='HIDDEN SCRIPT MUST NEVER REACH METADATA', rate=None):
    source = tmp_path / 'original.wav'; source.write_bytes(audio_bytes)
    tagged = tmp_path / 'tagged.mp3'
    extra = ['-ar', str(rate)] if rate else []
    subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-i', str(source), *extra, '-id3v2_version', '3',
        '-metadata', 'comment=' + secret, str(tagged)], check=True)
    return tagged.read_bytes(), secret


@pytest.mark.parametrize('rate', [None, 24000])
def test_strip_removes_tags_without_reencoding(tmp_path, audio_bytes, rate):
    from app.audio import strip_generated_mp3, mpeg_layer3_frames_cover
    tagged, secret = _tagged_mp3(tmp_path, audio_bytes, rate=rate)
    assert secret.encode() in tagged and not mpeg_layer3_frames_cover(tagged)
    stripped = asyncio.run(strip_generated_mp3(tagged))
    assert secret.encode() not in stripped and mpeg_layer3_frames_cover(stripped)
    assert stripped[:3] != b'ID3' and stripped[-128:-125] != b'TAG'
    # Frames are copied, so the audio payload is byte-identical to the original frames.
    assert stripped[-2000:] in tagged


def test_strip_rejects_non_mp3_and_trailing_bytes(tmp_path, audio_bytes):
    from app.audio import strip_generated_mp3, mpeg_layer3_frames_cover
    tagged, _ = _tagged_mp3(tmp_path, audio_bytes)
    stripped = asyncio.run(strip_generated_mp3(tagged))
    assert not mpeg_layer3_frames_cover(stripped + b'hidden trailer')
    assert not mpeg_layer3_frames_cover(b'\xff\xfb\x90\x00' + b'x' * 10)
    assert not mpeg_layer3_frames_cover(b'')
    with pytest.raises(ValueError):
        asyncio.run(strip_generated_mp3(b'not audio at all' * 50))
