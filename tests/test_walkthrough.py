"""Walkthrough state uses authenticated, revision-checked profile transactions."""
import pytest
from app.models import OnboardingProgress
from pydantic import ValidationError
from test_auth import accounts  # noqa: F401
from test_persistence import repository_factory  # noqa: F401


def test_walkthrough_account_resume_skip_and_stale_writes(accounts):
    a, b, _ = accounts
    initial = a.get('/api/onboarding').json()
    assert initial['walkthrough'] == 0
    state = a.post('/api/onboarding/walkthrough', json={'revision': 0, 'step': 1}).json()
    assert state['walkthrough'] == 1
    assert state['profile']['onboarding_status'] == 'not_started'
    assert a.get('/api/onboarding').json() == state
    assert b.get('/api/onboarding').json()['walkthrough'] == 0
    assert a.post('/api/onboarding/walkthrough', json={'revision': 0, 'step': 2}).status_code == 409
    state = a.post('/api/onboarding/walkthrough', json={'revision': state['revision'], 'step': 'skipped'}).json()
    assert state['walkthrough'] is None
    assert state['profile']['onboarding']['walkthrough'] == 'skipped'
    assert a.post('/api/onboarding/walkthrough', json={'revision': 0, 'step': 0}).json() == state
    started = a.post('/api/onboarding/start', json={'revision': state['revision']}).json()
    assert started['welcome'] == 'introduction'  # AUD-43/44 welcome survives intro writes.
    assert started['walkthrough'] is None


def test_walkthrough_complete_back_and_existing_onboarding(accounts):
    a, b, _ = accounts
    state = a.get('/api/onboarding').json()
    for step in (1, 0, 1, 2, 'complete'):
        state = a.post('/api/onboarding/walkthrough', json={'revision': state['revision'], 'step': step}).json()
        assert state['walkthrough'] == (step if type(step) is int else None)
    assert state['profile']['onboarding']['walkthrough'] == 'complete'
    assert a.get('/api/onboarding').json()['walkthrough'] is None
    state = b.post('/api/onboarding/start', json={'revision': 0}).json()
    assert state['walkthrough'] is None
    assert b.post('/api/onboarding/walkthrough', json={'revision': state['revision'], 'step': 1}).json() == state


@pytest.mark.parametrize('step', [-1, 3, 'invalid', None, {}, True])
def test_walkthrough_rejects_invalid_steps(accounts, step):
    a, _, _ = accounts
    response = a.post('/api/onboarding/walkthrough', json={'revision': 0, 'step': step})
    assert response.status_code == 422


def test_walkthrough_legacy_checkpoint():
    assert OnboardingProgress.model_validate({'stage': 'needs', 'revision': 8}).walkthrough == 0
    with pytest.raises(ValidationError):
        OnboardingProgress(walkthrough=3)


def test_walkthrough_existing_and_reconstructed_profiles(repository_factory, tmp_path):
    from app.main import create_app
    from app.config import Settings
    from conftest import ASGIClient, FakeProvider
    repo = repository_factory()
    profile = repo.profile()
    profile.onboarding.walkthrough = 2
    repo.save_profile(profile)
    provider = FakeProvider()
    client = ASGIClient(create_app(Settings(data_dir=tmp_path, _env_file=None), provider,
                                  repository=repository_factory()), provider)
    assert client.get('/api/onboarding').json()['walkthrough'] == 2
    for status in ('in_progress', 'profile_saved', 'complete'):
        profile.onboarding_status = status
        repo.save_profile(profile)
        state = client.get('/api/onboarding').json()
        assert state['walkthrough'] is None
        assert client.post('/api/onboarding/walkthrough', json={'revision': 0, 'step': 0}).json() == state


def test_walkthrough_requires_authentication(accounts):
    from conftest import ASGIClient
    a, _, calls = accounts
    response = ASGIClient(a.app, a.provider).post('/api/onboarding/walkthrough', json={'revision': 0, 'step': 'skipped'})
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'
    assert calls == []
