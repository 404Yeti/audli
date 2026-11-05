"""Recording storage isolation: temporary learner audio lives in its own store (own bucket and credentials in production, own folder locally),
can never be signed or served, and the lesson-audio credential and the recording credential are different identities."""
import httpx
import pytest
from pydantic import SecretStr
from uuid import uuid4
from sqlalchemy import select

from app.config import Settings
from app.listening import schema as l
from app.listening.objects import (RECORDING_PREFIX, LocalObjectStore, S3ObjectStore, build_recording_store, build_store)
from app.main import create_app
from conftest import ASGIClient, FakeProvider
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import begin, journey, upload  # noqa: F401  (fixtures)

KEY = RECORDING_PREFIX + 'attempt/exercise/g1-abcdefghijklmnopqrstuvwx.wav'
S3 = dict(listening_storage='s3', listening_s3_endpoint='https://s3.example.com', listening_s3_bucket='stg-lessons', listening_s3_access_key='LESSONKEY',
          listening_s3_secret_key='lessonsecret', listening_recordings_bucket='stg-recordings', listening_recordings_access_key='RECORDKEY',
          listening_recordings_secret_key='recordsecret', _env_file=None)


def test_a_recording_key_can_never_be_signed_or_verified(tmp_path):
    local = LocalObjectStore(tmp_path, b'k' * 32)
    local.put(KEY, b'audio', 'audio/wav')
    with pytest.raises(ValueError, match='never signed'):
        local.signed_url(KEY)
    expires = 2_000_000_000
    assert local.verify(KEY, expires, local.sign(KEY, expires), now=1_999_999_000) is None        # even a correctly forged signature serves nothing
    s3 = S3ObjectStore('https://s3.example.com', 'bucket', 'auto', 'AK', 'SK')
    with pytest.raises(ValueError, match='never signed'):
        s3.signed_url(KEY)
    assert 'X-Amz-Signature' in s3.signed_url('lessons/a/b.mp3')                                    # ordinary lesson keys still sign


def test_the_media_route_cannot_serve_a_recording_even_with_a_valid_signature(journey):
    j = journey
    secret = 'x' * 24
    app = create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, listening_signing_secret=SecretStr(secret), _env_file=None),
                     FakeProvider(), repository=j.repo)
    client = ASGIClient(app, None)
    lessons = LocalObjectStore(j.tmp / 'listening_objects', secret.encode())
    lessons.put(KEY, b'pretend this is a learner recording', 'audio/wav')                             # worst case: a recording in the lesson store
    expires = 4_000_000_000
    response = client.get(f'/api/listening/media/{KEY}', params={'exp': expires, 'sig': lessons.sign(KEY, expires)})
    assert response.status_code == 404 and b'pretend' not in response.content


def test_uploads_land_in_the_recording_store_only(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    assert upload(j.client, view).status_code == 200
    assert any((j.tmp / 'listening_recordings' / 'listening-tmp').rglob('*.wav'))
    assert not (j.tmp / 'listening_objects' / 'listening-tmp').exists()                          # the lesson store never sees a recording
    with j.engine.connect() as db:
        key = db.execute(select(l.spoken.c.recording_key)).scalar_one()
    assert key.startswith(RECORDING_PREFIX) and j.recordings.exists(key)


def test_local_stores_are_separate_folders(tmp_path):
    settings = Settings(_env_file=None)
    lessons, recordings = build_store(settings, tmp_path), build_recording_store(settings, tmp_path)
    assert lessons.root != recordings.root and recordings.root.name == 'listening_recordings' and lessons.root.name == 'listening_objects'


def test_in_production_the_two_stores_are_different_buckets_with_different_credentials():
    settings = Settings(**S3)
    lessons, recordings = build_store(settings, None), build_recording_store(settings, None)
    assert (lessons.bucket, lessons.access_key, lessons.secret_key) == ('stg-lessons', 'LESSONKEY', 'lessonsecret')
    assert (recordings.bucket, recordings.access_key, recordings.secret_key) == ('stg-recordings', 'RECORDKEY', 'recordsecret')
    own = Settings(**{**S3, 'listening_recordings_endpoint': 'https://records.example.com'})
    assert build_recording_store(own, None).base == 'https://records.example.com' and build_store(own, None).base == 'https://s3.example.com'


@pytest.mark.parametrize('change,message', [
    ({'listening_recordings_bucket': ''}, 'separate recordings bucket'),
    ({'listening_recordings_access_key': None}, 'separate recordings bucket'),
    ({'listening_recordings_secret_key': None}, 'separate recordings bucket'),
    ({'listening_recordings_bucket': 'stg-lessons'}, 'different bucket'),
    ({'listening_recordings_access_key': 'LESSONKEY'}, 'different bucket'),
    ({'listening_recordings_endpoint': 'http://insecure'}, 'https')])
def test_a_shared_or_missing_recordings_identity_is_refused_at_startup(change, message):
    with pytest.raises(Exception, match=message):
        Settings(listening_enabled=True, **{**S3, **change})


def test_each_store_signs_with_its_own_credential():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers['authorization']))
        return httpx.Response(200)
    transport = httpx.Client(transport=httpx.MockTransport(handler))
    settings = Settings(**S3)
    lessons, recordings = build_store(settings, None), build_recording_store(settings, None)
    lessons.client = recordings.client = transport
    recordings.put(KEY, b'x', 'audio/wav')
    lessons.put('lessons/a/b.mp3', b'x', 'audio/mpeg')
    assert 'Credential=RECORDKEY/' in seen[0][1] and 'Credential=LESSONKEY/' in seen[1][1] and 'LESSONKEY' not in seen[0][1] and 'RECORDKEY' not in seen[1][1]
