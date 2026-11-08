#!/usr/bin/env python3
"""Minimal authenticated production smoke test and cross-account privacy check for a curated lesson. Read-only: it only issues GET requests.

    export AUDLI_SMOKE_TOKEN_A=...   # a signed-in session access token for the CURATOR account (type it; never paste it into chat)
    export AUDLI_SMOKE_TOKEN_B=...   # a signed-in session access token for a DIFFERENT, ordinary account
    python scripts/listening_smoke.py draft     <lesson_id>     # BEFORE publishing: only the curator can see it
    python scripts/listening_smoke.py published <lesson_id>     # AFTER publishing: the other account can see and open it too

How to get a token: sign in on the site, open the browser dev tools -> Application -> Local Storage (or the Network tab), and copy the access_token of the
Supabase session. Tokens expire in about an hour. They are read from the environment only and are never printed or written.
Exit code 0 = every check passed; 1 = a check failed (do not publish / unpublish); 2 = bad usage or configuration."""
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = 'https://audli-api.onrender.com'


def make_fetch(base: str, tokens: dict):
    def fetch(who: str, path: str):
        request = urllib.request.Request(base + path, headers={'Authorization': f'Bearer {tokens[who]}', 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as problem:
            return problem.code, None
    return fetch


def run_checks(mode: str, lesson_id: str, fetch, out=print) -> bool:
    ok = True

    def check(label, passed, detail=''):
        nonlocal ok
        ok = ok and passed
        out(('PASS ' if passed else 'FAIL ') + label + (f' ({detail})' if detail and not passed else ''))

    def listed(who):
        status, body = fetch(who, '/api/listening/lessons')
        return status, isinstance(body, list) and any(item.get('lesson_id') == lesson_id for item in body)

    def opens(who):
        return fetch(who, f'/api/listening/lessons/{lesson_id}/attempts/current')[0]

    status, found = listed('A')
    check('curator: lessons list answers 200 (not 503)', status == 200, f'HTTP {status}')
    check('curator: the lesson is in the list', found)
    check('curator: the lesson opens', opens('A') == 200, f'HTTP {opens("A")}')
    status, found = listed('B')
    check('other account: lessons list answers 200', status == 200, f'HTTP {status}')
    if mode == 'draft':
        check('other account: the draft is NOT in their list', not found)
        code = opens('B')
        check('other account: opening the draft is denied (404)', code == 404, f'HTTP {code}')
    else:
        check('other account: the published lesson IS in their list', found)
        code = opens('B')
        check('other account: the published lesson opens', code == 200, f'HTTP {code}')
    return ok


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ('draft', 'published'):
        print(__doc__.split('\n\n')[0])
        return 2
    tokens = {'A': os.environ.get('AUDLI_SMOKE_TOKEN_A', ''), 'B': os.environ.get('AUDLI_SMOKE_TOKEN_B', '')}
    base = os.environ.get('AUDLI_API_URL', DEFAULT_URL).rstrip('/')
    if not all(tokens.values()) or tokens['A'] == tokens['B']:
        print('Set AUDLI_SMOKE_TOKEN_A (curator) and AUDLI_SMOKE_TOKEN_B (a different account): two distinct tokens are required.', file=sys.stderr)
        return 2
    if not base.startswith('https://') and not base.startswith('http://127.0.0.1'):
        print('AUDLI_API_URL must be https (or a loopback address)', file=sys.stderr)
        return 2
    status = urllib.request.urlopen(base + '/api/health', timeout=60).status
    print(('PASS' if status == 200 else 'FAIL') + f' health answers {status}')
    ok = run_checks(argv[0], argv[1], make_fetch(base, tokens)) and status == 200
    print('RESULT: ' + ('ALL CHECKS PASSED' if ok else 'FAILED: do not publish (or unpublish) and investigate'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
