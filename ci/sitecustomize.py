"""Fail-closed test networking; loaded only through CI test-step PYTHONPATH."""
import ipaddress
import os
import socket
import sys
from urllib.parse import urlsplit


def loopback(host):
    if host in ('localhost', b'localhost', None):
        return True
    try:
        address = ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except (ValueError, TypeError):
        return False


def validate_environment():
    # Production credentials are never necessary, even for mocked provider tests.
    for key in ('OPENAI_API_KEY', 'ELEVENLABS_API_KEY', 'AUDLI_OPENAI_API_KEY',
                'AUDLI_ELEVENLABS_API_KEY', 'SUPABASE_ACCESS_TOKEN', 'AUDLI_DATABASE_URL'):
        if os.environ.get(key):
            raise ValueError('CI received a forbidden production/provider environment value')
    url = os.environ.get('AUDLI_TEST_POSTGRES_URL')
    if url:
        target = urlsplit(url)
        if target.scheme not in ('postgresql', 'postgresql+psycopg', 'postgres') or not target.hostname or not loopback(target.hostname):
            raise ValueError('CI PostgreSQL must be disposable and local')


if os.environ.get('AUDLI_CI_OFFLINE') == '1':
    try:
        validate_environment()
    except ValueError:
        sys.stderr.write('CI safety check rejected environment configuration.\n')
        os._exit(78)  # Ordinary sitecustomize exceptions otherwise fail open.

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def allowed(connection, address):
        return connection.family == socket.AF_UNIX or (isinstance(address, tuple) and loopback(address[0]))

    def connect(connection, address):
        if not allowed(connection, address):
            raise PermissionError('CI test networking permits loopback only')
        return original_connect(connection, address)

    def connect_ex(connection, address):
        if not allowed(connection, address):
            raise PermissionError('CI test networking permits loopback only')
        return original_connect_ex(connection, address)

    def resolve(host, *args, **kwargs):
        if not loopback(host):
            raise PermissionError('CI test networking permits loopback only')
        return original_resolve(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = resolve
