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
