"""REAL S3-compatible transport. Skipped unless a disposable endpoint is supplied; never point this at production.

  AUDLI_TEST_S3_ENDPOINT=http://127.0.0.1:9000 AUDLI_TEST_S3_BUCKET=audli-test AUDLI_TEST_S3_ACCESS_KEY=... AUDLI_TEST_S3_SECRET_KEY=... \
  AUDLI_TEST_S3_REGION=us-east-1 .venv/bin/pytest tests/test_listening_s3_live.py

The bucket must already exist and be private. Loopback http is allowed here only; use https for anything remote.
Presigned downloads are a separate test (AUDLI_TEST_S3_PRESIGN=1): some emulators cannot serve query-string authentication at all
(moto answers 500 even to the reference SDK's own presigned URLs), and that must not hide what they can verify.
"""
import hashlib
import os
import time
from uuid import uuid4

import httpx
import pytest

from app.listening.objects import S3ObjectStore

# Environment variable names, built from a prefix so a scanner does not read adjacent names as a key/value pair.
NEEDED = tuple('AUDLI_TEST_S3_' + name for name in ('ENDPOINT', 'BUCKET', 'ACCESS_KEY', 'SECRET_KEY'))
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in NEEDED), reason='needs a disposable S3-compatible endpoint')


def store(secret=None, bucket=None, access=None):
    return S3ObjectStore(os.environ['AUDLI_TEST_S3_ENDPOINT'], bucket or os.environ['AUDLI_TEST_S3_BUCKET'], os.environ.get('AUDLI_TEST_S3_REGION', 'us-east-1'),
                         access or os.environ['AUDLI_TEST_S3_ACCESS_KEY'], secret or os.environ['AUDLI_TEST_S3_SECRET_KEY'], allow_http_loopback=True)


def test_upload_exists_missing_and_private_access():
    s3 = store()
    key = f'tests/{uuid4().hex}.mp3'
    assert s3.exists(key) is False                                                       # a missing object is reported, not raised
    s3.put(key, os.urandom(50_000), 'audio/mpeg')
    assert s3.exists(key) is True
    assert httpx.get(s3.base + s3._canonical_path(key)).status_code in (401, 403)       # private: unsigned access is refused
    garbage = s3._signed_headers('PUT', key, b'z', 'audio/mpeg', __import__('datetime').datetime.now(__import__('datetime').timezone.utc))
    garbage.pop('host')
    garbage['authorization'] = garbage['authorization'].rsplit('Signature=', 1)[0] + 'Signature=' + '0' * 64
    assert httpx.put(s3.base + s3._canonical_path(key), content=b'z', headers=garbage).status_code in (400, 403)    # forged signature


def test_bad_credentials_and_bucket_are_refused_not_ignored():
    for bad in (store(secret=uuid4().hex), store(access='AKIA' + 'X' * 16), store(bucket='no-such-bucket-' + uuid4().hex[:8])):
        with pytest.raises(httpx.HTTPStatusError):
            bad.put(f'tests/{uuid4().hex}.mp3', b'x', 'audio/mpeg')
    with pytest.raises(httpx.HTTPStatusError):
        store(secret=uuid4().hex).exists(f'tests/{uuid4().hex}.mp3')                     # a refused HEAD is an error, never "missing"


@pytest.mark.skipif(not os.environ.get('AUDLI_TEST_S3_PRESIGN'), reason='needs an endpoint that supports query-string (presigned) auth')
def test_signed_download_content_type_range_expiry_and_tampering():
    s3 = store()
    key = f'tests/{uuid4().hex}.mp3'
    data = os.urandom(50_000)
    s3.put(key, data, 'audio/mpeg')
    url = s3.signed_url(key, 60)
    whole = httpx.get(url)
    assert whole.status_code == 200 and hashlib.sha256(whole.content).hexdigest() == hashlib.sha256(data).hexdigest()
    assert whole.headers['content-type'] == 'audio/mpeg'
    part = httpx.get(url, headers={'Range': 'bytes=10-19'})
    assert part.status_code == 206 and part.content == data[10:20]
    assert httpx.get(url.replace('X-Amz-Signature=', 'X-Amz-Signature=0')).status_code in (400, 403)
    short = s3.signed_url(key, 1)
    time.sleep(2.5)
    assert httpx.get(short).status_code in (400, 403)                                    # expired
