"""scripts/listening_smoke.py: the decision logic with a fake HTTP layer (no network, no tokens)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('listening_smoke', Path(__file__).resolve().parents[1] / 'scripts' / 'listening_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
LESSON = 'L1'


def fake(a_sees=True, b_sees=False, b_open=404, a_status=200):
    def fetch(who, path):
        if path == '/api/listening/lessons':
            sees = a_sees if who == 'A' else b_sees
            return (a_status if who == 'A' else 200), ([{'lesson_id': LESSON}] if sees else [{'lesson_id': 'other'}])
        return (200 if who == 'A' else b_open), {}
    return fetch


def test_draft_passes_only_when_the_other_account_cannot_see_or_open_it():
    assert smoke.run_checks('draft', LESSON, fake(), out=lambda *_: None)
    assert not smoke.run_checks('draft', LESSON, fake(b_sees=True), out=lambda *_: None)          # leaked into another list
    assert not smoke.run_checks('draft', LESSON, fake(b_open=200), out=lambda *_: None)          # openable by another account
    assert not smoke.run_checks('draft', LESSON, fake(a_status=503), out=lambda *_: None)


def test_published_passes_only_when_the_other_account_can_see_and_open_it():
    assert smoke.run_checks('published', LESSON, fake(b_sees=True, b_open=200), out=lambda *_: None)
    assert not smoke.run_checks('published', LESSON, fake(b_sees=False, b_open=200), out=lambda *_: None)
    assert not smoke.run_checks('published', LESSON, fake(b_sees=True, b_open=404), out=lambda *_: None)


def test_refuses_without_two_distinct_tokens_and_never_prints_them(monkeypatch, capsys):
    monkeypatch.setenv('AUDLI_SMOKE_TOKEN_A', 'SECRET-TOKEN-1')
    monkeypatch.setenv('AUDLI_SMOKE_TOKEN_B', 'SECRET-TOKEN-1')
    assert smoke.main(['draft', LESSON]) == 2
    captured = capsys.readouterr()
    assert 'SECRET-TOKEN-1' not in captured.out + captured.err
