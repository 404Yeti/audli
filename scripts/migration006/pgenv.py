#!/usr/bin/env python3
"""Translate a database URL held in an environment variable into libpq environment variables.

Used only as `eval "$(python3 pgenv.py VARIABLE_NAME)"` inside the migration scripts so that credentials travel through
the environment (never argv, never the terminal). On any problem it prints a message that does not contain the URL.

  pgenv.py NAME                 -> export PGHOST=... PGPORT=... PGUSER=... PGPASSWORD=... PGDATABASE=... [PGSSLMODE=...]
  pgenv.py NAME --fingerprint   -> 12 hex characters identifying host:port/database (safe to print, not reversible)
  pgenv.py NAME --label         -> masked host and database name (safe to print)
  pgenv.py NAME --host          -> the host name (for the loopback check only; the scripts never print it)
  pgenv.py NAME --redact        -> copy stdin to stdout with the password, user name, host and IP addresses removed
"""
import hashlib
import os
import shlex
import sys
from urllib.parse import parse_qs, unquote, urlsplit


def fail(message):
    print(f'pgenv: {message}', file=sys.stderr)
    sys.exit(2)


def parse(name):
    value = os.environ.get(name)
    if not value:
        fail(f'environment variable {name} is not set')
    value = value.replace('postgresql+psycopg://', 'postgresql://', 1).replace('postgres://', 'postgresql://', 1)
    parts = urlsplit(value)
    if parts.scheme != 'postgresql' or not parts.hostname or not parts.path.strip('/'):
        fail(f'{name} is not a complete postgresql:// URL (host and database are required)')
    return {
        'PGHOST': parts.hostname, 'PGPORT': str(parts.port or 5432), 'PGUSER': unquote(parts.username or ''),
        'PGPASSWORD': unquote(parts.password or ''), 'PGDATABASE': unquote(parts.path.strip('/')),
        **({'PGSSLMODE': parse_qs(parts.query)['sslmode'][0]} if 'sslmode' in parse_qs(parts.query) else {}),
    }


def fingerprint(env):
    return hashlib.sha256(f"{env['PGHOST'].lower()}:{env['PGPORT']}/{env['PGDATABASE']}".encode()).hexdigest()[:12]


def label(env):
    host = env['PGHOST']
    masked = host if host in ('localhost', '127.0.0.1', '::1') else (host[:3] + '…' + host[-12:] if len(host) > 18 else host[:3] + '…')
    return f"host={masked} port={env['PGPORT']} db={env['PGDATABASE']}"


if __name__ == '__main__':
    if len(sys.argv) < 2:
        fail('usage: pgenv.py VARIABLE_NAME [--fingerprint|--label]')
    settings = parse(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else ''
    if mode == '--fingerprint':
        print(fingerprint(settings))
    elif mode == '--label':
        print(label(settings))
    elif mode == '--host':
        print(settings['PGHOST'])
    elif mode == '--redact':
        import re
        text = sys.stdin.read()
        for key, placeholder in (('PGPASSWORD', '[password]'), ('PGUSER', '[user]'), ('PGHOST', '[host]')):
            if settings.get(key):
                text = text.replace(settings[key], placeholder)
        sys.stdout.write(re.sub(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', '[ip]', text))
    elif mode == '':
        for key, value in settings.items():
            print(f'export {key}={shlex.quote(value)}')
    else:
        fail(f'unknown option {mode}')
