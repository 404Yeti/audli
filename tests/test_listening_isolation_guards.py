"""AUD-103 regression guards that complement the two-account HTTP tests (test_listening_journey, _generation_api, _library, _allowlist).

They check properties that do not depend on any UI: the server never takes a learner identity from the client, signed media is bound to
its key, and a signed URL's lifetime (the documented limitation) is exactly its expiry and no longer.
"""
import ast
import time
from pathlib import Path

from app.listening.api import SIGNED_URL_SECONDS
from app.listening.objects import LocalObjectStore, MAX_TTL

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'app/listening/api.py'
CLIENT_IDENTITY = {'learner_id', 'user_id', 'owner_id', 'owner', 'learner', 'account_id'}


def route_functions():
    tree = ast.parse(API.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ('get', 'post', 'put', 'patch', 'delete')
                                                     for d in node.decorator_list):
            yield node


def test_no_listening_route_accepts_a_learner_identity_from_the_client():
    routes = list(route_functions())
    assert len(routes) >= 14, 'the route inventory changed; review isolation coverage before adjusting this guard'
    for route in routes:
        params = {a.arg for a in route.args.args + route.args.kwonlyargs}
        assert not params & CLIENT_IDENTITY, f'{route.name} takes a client-supplied identity: {params & CLIENT_IDENTITY}'
        # the path template may not carry one either
        for decorator in route.decorator_list:
            path = decorator.args[0].value if isinstance(decorator, ast.Call) and decorator.args and isinstance(decorator.args[0], ast.Constant) else ''
            assert not any(f'{{{name}}}' in str(path) for name in CLIENT_IDENTITY), f'{route.name} path carries an identity'


def test_every_listening_route_that_reads_learner_data_uses_the_server_verified_identity():
    source = API.read_text()
    for route in route_functions():
        segment = ast.get_source_segment(source, route)
        if any(word in segment for word in ('store.', 'spoken.', 'genjobs.', '_view(', '_generation_view(')) and route.name != 'media':
            assert 'repo.learner_id' in segment or 'user_id' in segment or '_view(' in segment or '_generation_view(' in segment, f'{route.name} does not scope by learner'


def test_signed_media_is_bound_to_its_key_and_expiry(tmp_path):
    store = LocalObjectStore(tmp_path, b'k' * 32)
    now = time.time()
    key_a, key_b = 'lessons/aaaa/audio.mp3', 'lessons/bbbb/audio.mp3'
    url = store.signed_url(key_a, SIGNED_URL_SECONDS, now=now)
    expires = int(url.split('exp=')[1].split('&')[0]); sig = url.split('sig=')[1]
    store.put(key_a, b'a', 'audio/mpeg'); store.put(key_b, b'b', 'audio/mpeg')
    assert store.verify(key_a, expires, sig, now=now) is not None
    assert store.verify(key_b, expires, sig, now=now) is None                      # a signature for A's object never opens B's
    assert store.verify(key_a, expires + 60, sig, now=now) is None                 # the expiry cannot be extended
    assert store.verify(key_a, expires, sig[:-1] + ('0' if sig[-1] != '0' else '1'), now=now) is None
    assert store.verify(key_a, int(now) - 1, store.sign(key_a, int(now) - 1), now=now) is None


def test_a_signed_url_stays_valid_until_it_expires_and_never_longer__documented_limitation(tmp_path):
    """Ownership is checked when the URL is issued, not when it is fetched: a URL already handed out works for anyone holding it until
    expiry (SIGNED_URL_SECONDS). This is the accepted limitation recorded in docs/LISTENING-ISOLATION-VERIFICATION.md."""
    store = LocalObjectStore(tmp_path, b'k' * 32)
    key, now = 'lessons/cccc/audio.mp3', time.time()
    store.put(key, b'x', 'audio/mpeg')
    url = store.signed_url(key, SIGNED_URL_SECONDS, now=now)
    expires = int(url.split('exp=')[1].split('&')[0]); sig = url.split('sig=')[1]
    assert store.verify(key, expires, sig, now=now + SIGNED_URL_SECONDS - 5) is not None         # still works for the holder inside the window
    assert store.verify(key, expires, sig, now=now + SIGNED_URL_SECONDS + 5) is None             # and stops at expiry
    assert SIGNED_URL_SECONDS <= MAX_TTL and SIGNED_URL_SECONDS <= 300                           # short by design
