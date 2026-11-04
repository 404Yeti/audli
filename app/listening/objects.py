"""Private object storage for 2.0 lesson audio.

Audio bytes never go in PostgreSQL. A learner receives only a short-lived signed URL, minted after an ownership check; object
keys are never listed in learner responses. Two backends share one interface:

* LocalObjectStore: files under a directory, served by this API at /api/listening/media/{key} with an HMAC-signed, expiring
  link. Development and tests only: production must use an S3-compatible bucket (the filesystem there is ephemeral).
* S3ObjectStore: any S3-compatible endpoint, using SigV4 (stdlib hmac, no SDK dependency). Presigned GET for playback.
"""
import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_./-]{0,198}$')
MAX_TTL = 3600


def valid_key(key: str) -> bool:
    return bool(KEY.match(key)) and '..' not in key and '//' not in key and not key.endswith('/')


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def signed_url(self, key: str, ttl_seconds: int = 300, now: float | None = None) -> str: ...


def _check(key: str, ttl: int) -> None:
    if not valid_key(key):
        raise ValueError('Invalid object key')
    if not 1 <= ttl <= MAX_TTL:
        raise ValueError('Signed URL lifetime must be between 1 and 3600 seconds')


class LocalObjectStore:
    def __init__(self, root: Path, secret: bytes, public_path: str = '/api/listening/media'):
        if len(secret) < 16:
            raise ValueError('Signing secret is too short')
        self.root, self.secret, self.public_path = Path(root), secret, public_path.rstrip('/')

    def _path(self, key: str) -> Path:
        if not valid_key(key):
            raise ValueError('Invalid object key')
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError('Invalid object key')
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def sign(self, key: str, expires: int) -> str:
        return hmac.new(self.secret, f'{key}\n{expires}'.encode(), hashlib.sha256).hexdigest()

    def signed_url(self, key: str, ttl_seconds: int = 300, now: float | None = None) -> str:
        _check(key, ttl_seconds)
        expires = int((time.time() if now is None else now) + ttl_seconds)
        return f'{self.public_path}/{quote(key, safe="/")}?exp={expires}&sig={self.sign(key, expires)}'

    def verify(self, key: str, expires: int, signature: str, now: float | None = None) -> Path | None:
        """The file for a valid, unexpired signature, else None. Constant-time comparison."""
        if not valid_key(key) or (time.time() if now is None else now) > expires or expires - (time.time() if now is None else now) > MAX_TTL + 5:
            return None
        if not hmac.compare_digest(self.sign(key, expires), signature or ''):
            return None
        path = self._path(key)
        return path if path.is_file() else None


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class S3ObjectStore:
    """SigV4 for S3-compatible endpoints (path-style addressing; works with AWS, R2, MinIO). Not exercised against a live bucket
    in this repository's tests: the signing is checked against AWS's published example, the network calls are not."""
    def __init__(self, endpoint: str, bucket: str, region: str, access_key: str, secret_key: str, client=None, path_style: bool = True):
        parts = urlsplit(endpoint)
        if parts.scheme != 'https' or not parts.netloc or parts.path not in ('', '/') or parts.query or parts.fragment:
            raise ValueError('S3 endpoint must be an https origin')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
            raise ValueError('Invalid bucket name')
        self.host, self.base, self.bucket, self.region = parts.netloc, f'https://{parts.netloc}', bucket, region
        self.access_key, self.secret_key, self.client, self.path_style = access_key, secret_key, client, path_style

    def _signing_key(self, day: str) -> bytes:
        key = _hmac(('AWS4' + self.secret_key).encode(), day)
        for part in (self.region, 's3', 'aws4_request'):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        return key

    def _canonical_path(self, key: str) -> str:
        # Path-style (host/bucket/key) by default; virtual-hosted style (bucket in the endpoint host) keeps just /key.
        return ('/' + quote(self.bucket, safe='') if self.path_style else '') + '/' + quote(key, safe='/-_.~')

    def presign_get(self, key: str, ttl_seconds: int, at: datetime) -> str:
        _check(key, ttl_seconds)
        return self._presign(key, ttl_seconds, at)

    def _presign(self, key: str, ttl_seconds: int, at: datetime) -> str:
        stamp, day = at.strftime('%Y%m%dT%H%M%SZ'), at.strftime('%Y%m%d')
        scope = f'{day}/{self.region}/s3/aws4_request'
        query = {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256', 'X-Amz-Credential': f'{self.access_key}/{scope}', 'X-Amz-Date': stamp,
                 'X-Amz-Expires': str(ttl_seconds), 'X-Amz-SignedHeaders': 'host'}
        canonical_query = '&'.join(f'{quote(k, safe="-_.~")}={quote(v, safe="-_.~")}' for k, v in sorted(query.items()))
        path = self._canonical_path(key)
        canonical = '\n'.join(['GET', path, canonical_query, f'host:{self.host}\n', 'host', 'UNSIGNED-PAYLOAD'])
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        signature = hmac.new(self._signing_key(day), to_sign.encode(), hashlib.sha256).hexdigest()
        return f'{self.base}{path}?{canonical_query}&X-Amz-Signature={signature}'

    def signed_url(self, key: str, ttl_seconds: int = 300, now: float | None = None) -> str:
        return self.presign_get(key, ttl_seconds, datetime.fromtimestamp(time.time() if now is None else now, timezone.utc))

    def _signed_headers(self, method: str, key: str, payload: bytes, content_type: str | None, at: datetime) -> dict:
        stamp, day = at.strftime('%Y%m%dT%H%M%SZ'), at.strftime('%Y%m%d')
        digest = hashlib.sha256(payload).hexdigest()
        headers = {'host': self.host, 'x-amz-content-sha256': digest, 'x-amz-date': stamp}
        if content_type:
            headers['content-type'] = content_type
        names = sorted(headers)
        canonical = '\n'.join([method, self._canonical_path(key), '', ''.join(f'{n}:{headers[n]}\n' for n in names), ';'.join(names), digest])
        scope = f'{day}/{self.region}/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        signature = hmac.new(self._signing_key(day), to_sign.encode(), hashlib.sha256).hexdigest()
        headers['authorization'] = (f'AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, '
                                    f'SignedHeaders={";".join(names)}, Signature={signature}')
        return headers

    def put(self, key: str, data: bytes, content_type: str) -> None:
        import httpx
        if not valid_key(key):
            raise ValueError('Invalid object key')
        headers = self._signed_headers('PUT', key, data, content_type, datetime.now(timezone.utc))
        headers.pop('host')
        response = (self.client or httpx).put(self.base + self._canonical_path(key), content=data, headers=headers, timeout=30)
        response.raise_for_status()

    def exists(self, key: str) -> bool:
        import httpx
        headers = self._signed_headers('HEAD', key, b'', None, datetime.now(timezone.utc))
        headers.pop('host')
        response = (self.client or httpx).head(self.base + self._canonical_path(key), headers=headers, timeout=15)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True


def build_store(settings, data_dir: Path) -> ObjectStore:
    if settings.listening_storage == 's3':
        return S3ObjectStore(settings.listening_s3_endpoint, settings.listening_s3_bucket, settings.listening_s3_region,
                             settings.listening_s3_access_key.get_secret_value(), settings.listening_s3_secret_key.get_secret_value())
    import secrets
    secret = (settings.listening_signing_secret.get_secret_value().encode() if settings.listening_signing_secret
              else secrets.token_bytes(32))  # development only: links die with the process
    return LocalObjectStore(data_dir / 'listening_objects', secret)
