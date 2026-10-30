"""AUD-96: completed learning quotas are separate from operational admission.

Same repository cases run on SQLite and disposable PostgreSQL. Auth and AI are mocked.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import Settings
from app.lesson_limit import LessonCooldown, WINDOW, GRACE_ACTION_LIMIT
from app.main import create_app
from app.storage import schema as s
from conftest import FakeProvider, ASGIClient
from test_persistence import repository_factory
from test_lesson_limit import make, T0
from test_auth import accounts_with, USER_A, USER_B


def complete(repo, started, completed):
    lesson = make(started)
    repo.create_lesson(lesson, limit_mode='enforce')
    lesson.status, lesson.phase, lesson.completed_at = 'completed', 'COMPLETED', completed
    repo.save_lesson(lesson, lesson.revision)
    return lesson


def fill(repo, count=10):
    return [complete(repo, T0 + timedelta(minutes=i), T0 + timedelta(minutes=i, seconds=30)) for i in range(count)]


def test_ten_completions_rolling_from_completion_not_start(repository_factory):
    repo = repository_factory()
    history = fill(repo)
    at = T0 + timedelta(hours=1)
    before = [repo.lesson(item.id).model_dump() for item in history]
    with pytest.raises(LessonCooldown) as refused:
        repo.create_lesson(make(at), limit_mode='enforce')
    assert refused.value.next_eligible_at == T0 + WINDOW + timedelta(seconds=30)
    assert repo.eligibility('enforce', at).next_available_at == refused.value.next_eligible_at
    assert repo.cooldown_until(T0 + WINDOW) == refused.value.next_eligible_at
    assert repo.cooldown_until(T0 + WINDOW + timedelta(seconds=30)) is None
    repo.create_lesson(make(T0 + WINDOW + timedelta(seconds=30)), limit_mode='enforce')
    assert [repo.lesson(item.id).model_dump() for item in history] == before


def test_incomplete_expired_and_paused_are_not_charged(repository_factory):
    repo = repository_factory()
    fill(repo, 9)
    at = T0 + timedelta(hours=1)
    for i in range(4):
        lesson = make(at + timedelta(minutes=i))
        repo.create_lesson(lesson, limit_mode='enforce')
        lesson.status = 'paused'
        repo.save_lesson(lesson, lesson.revision)
        assert repo.cooldown_until(at) is None
        with pytest.raises(ValueError, match='Resume'):
            repo.create_lesson(make(at), limit_mode='enforce')
        repo.expire_lesson(lesson.id, at)
    last = complete(repo, at + timedelta(hours=1), at + timedelta(hours=1, seconds=20))
    with pytest.raises(LessonCooldown):
        repo.create_lesson(make(last.completed_at), limit_mode='enforce')


def test_new_lesson_rebinds_grace_counters_without_changing_schema006_window(repository_factory):
    repo = repository_factory()
    first = make(T0)
    repo.create_lesson(first, limit_mode='enforce')
    assert [repo.take_grace_slot(first.id) for _ in range(GRACE_ACTION_LIMIT)][-1] == GRACE_ACTION_LIMIT
    repo.expire_lesson(first.id, T0 + timedelta(minutes=14))
    second = make(T0 + timedelta(minutes=15))
    repo.create_lesson(second, limit_mode='enforce')
    with repo.read() as db:
        row = db.execute(select(s.lesson_entitlements).where(s.lesson_entitlements.c.user_id == repo.learner_id)).mappings().one()
    assert row['last_lesson_id'] == second.id
    assert row['grace_actions'] == 0
    assert repo.take_grace_slot(first.id) is None
    assert repo.take_grace_slot(second.id) == 1
    assert [repo.take_grace_slot(second.id) for _ in range(GRACE_ACTION_LIMIT-1)][-1] == GRACE_ACTION_LIMIT
    assert repo.take_grace_slot(second.id) is None


def test_reduced_limit_waits_for_enough_completions(repository_factory):
    repo = repository_factory()
    fill(repo)
    repo.lesson_completed_limit = 3
    assert repo.cooldown_until(T0 + timedelta(hours=1)) == T0 + WINDOW + timedelta(minutes=7, seconds=30)


def test_other_accounts_do_not_share_completion_counts(repository_factory):
    first, second = repository_factory(), repository_factory(str(uuid4()))
    fill(first)
    assert second.cooldown_until(T0 + timedelta(hours=1)) is None
    second.create_lesson(make(T0 + timedelta(hours=1)), limit_mode='enforce')


def test_verified_developer_exemption_cannot_be_requested_by_another_account(repository_factory, tmp_path, monkeypatch):
    monkeypatch.setattr('app.lesson_api.now', lambda: T0 + timedelta(hours=1))
    accounts = accounts_with(tmp_path, repository_factory, lesson_limit_mode='enforce', developer_user_id=USER_A)
    a, b, _ = next(accounts)
    try:
        for client, owner in [(a, USER_A), (b, USER_B)]:
            assert client.get('/api/profile').status_code == 200
            scoped = client.app.state.repository.for_learner(owner)
            fill(scoped)
            if owner == USER_A:
                fill(scoped)  # More than ten completions; still operationally guarded.
        assert a.get('/api/eligibility').json()['eligible'] is True
        assert b.get('/api/eligibility', headers={'X-Learner-Id': USER_A, 'X-Developer-User-Id': USER_A}).json()['reason'] == 'cooldown'
        assert b.post('/api/lessons/start', json={'developer': True, 'learner_id': USER_A}).status_code == 429
        assert ASGIClient(a.app, a.provider).get('/api/eligibility').status_code == 401
    finally:
        accounts.close()


def test_local_identity_cannot_activate_developer_exemption(repository_factory, tmp_path):
    repo = repository_factory()
    create_app(Settings(data_dir=tmp_path, lesson_limit_mode='enforce', developer_user_id=repo.learner_id, _env_file=None), FakeProvider(), repo)
    assert repo.developer_user_id is None
    fill(repo)
    assert repo.cooldown_until(T0 + timedelta(hours=1)) is not None


@pytest.mark.parametrize('value', [0, -1, 101, 'invalid'])
def test_quota_configuration_is_strictly_bounded(value):
    with pytest.raises(ValueError):
        Settings(lesson_completed_limit=value, _env_file=None)


def test_developer_identity_must_be_a_uuid():
    with pytest.raises(ValueError):
        Settings(developer_user_id='email-or-browser-label', _env_file=None)
    assert Settings(developer_user_id='', _env_file=None).developer_user_id is None


def test_verified_developer_still_has_one_open_lesson_and_deadlines(repository_factory, tmp_path, monkeypatch, audio_bytes):
    clock = [T0]
    for target in ('app.lesson_api.now', 'app.limits.now', 'app.storage.repository.utcnow'):
        monkeypatch.setattr(target, lambda: clock[0])
    accounts = accounts_with(tmp_path, repository_factory, lesson_limit_mode='enforce', developer_user_id=USER_A)
    a, _, _ = next(accounts)
    try:
        a.get('/api/profile')
        scoped = a.app.state.repository.for_learner(USER_A)
        profile = scoped.profile(); profile.name = 'Test'; profile.onboarding_status = 'complete'; scoped.save_profile(profile)
        first = a.post('/api/lessons/start')
        assert first.status_code == 200
        lesson = first.json()
        assert a.post('/api/lessons/start').json()['id'] == lesson['id']
        clock[0] += timedelta(minutes=14)
        assert a.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']}, files={'audio': ('answer.wav', audio_bytes, 'audio/wav')}).status_code == 409
        assert scoped.lesson(lesson['id']).status == 'expired'
        assert a.post('/api/lessons/start').status_code == 200
    finally:
        accounts.close()


def test_preexisting_token_is_reverified_and_rejected_when_auth_denies_account(repository_factory, tmp_path):
    """Hosted banning still needs an approved real check; no accounts/tokens are changed here."""
    import asyncio
    import httpx
    from test_auth import settings_for, AccountClient
    blocked = [False]
    def auth(request):
        assert request.url.path == '/auth/v1/user'
        return httpx.Response(403, json={'msg': 'User is banned'}) if blocked[0] else httpx.Response(200, json={'id': USER_B, 'email_confirmed_at': 'yes', 'is_anonymous': False})
    identity = httpx.AsyncClient(transport=httpx.MockTransport(auth))
    provider = FakeProvider()
    app = create_app(settings_for(tmp_path), provider, repository_factory(None), auth_client=identity)
    client = AccountClient(app, provider, 'same-preexisting-test-token')
    try:
        assert client.get('/api/profile').status_code == 200
        blocked[0] = True
        assert client.get('/api/profile').status_code == 401
        assert client.post('/api/lessons/start').status_code == 401
    finally:
        asyncio.run(identity.aclose())
