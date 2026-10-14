"""Prove test isolation before running any application tests."""
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


@pytest.mark.parametrize('host', ['api.openai.com', 'api.elevenlabs.io', 'project.supabase.co', '8.8.8.8'])
def test_external_resolution_and_connections_are_blocked(host):
    with pytest.raises(PermissionError, match='loopback'):
        socket.getaddrinfo(host, 443)
    with socket.socket() as connection:
        with pytest.raises(PermissionError, match='loopback'):
            connection.connect((host, 443))


def test_loopback_still_resolves():
    assert socket.getaddrinfo('127.0.0.1', 5432)


@pytest.mark.parametrize('values', [
    {'AUDLI_TEST_POSTGRES_URL': 'postgresql://fixture:fixture@project.supabase.co/postgres'},
    {'AUDLI_DATABASE_URL': 'postgresql://fixture:fixture@127.0.0.1/postgres'},
    {'OPENAI_API_KEY': 'nonbillable-test-rejection'},
    {'AUDLI_ELEVENLABS_API_KEY': 'nonbillable-test-rejection'},
])
def test_forbidden_environment_cannot_start_python(values):
    environment = dict(os.environ, AUDLI_CI_OFFLINE='1', PYTHONPATH=str(Path(__file__).parent)) | values
    result = subprocess.run([sys.executable, '-c', 'raise AssertionError("must not execute")'],
                            env=environment, capture_output=True, timeout=10)
    assert result.returncode == 78
    assert b'CI safety check rejected' in result.stderr
