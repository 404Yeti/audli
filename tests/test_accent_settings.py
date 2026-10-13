"""AUD-30 Settings writes share authenticated preferences, never passage consent."""
import pytest
from uuid import uuid4
from app.models import AccentPreferences
from app.accent_practice import offer, authorized_accent
from app.lesson import Lesson, now
from test_auth import accounts, USER_A
from test_persistence import repository_factory
from conftest import ASGIClient


@pytest.mark.parametrize('accents', [['british'], ['australian'], ['irish'], ['british', 'australian', 'irish'], []])
def test_authenticated_settings_persist_and_drive_future_offers(accounts, accents):
    a, b, _ = accounts
    a.get('/api/profile'); b.get('/api/profile')
    repo = a.app.state.repository.for_learner(USER_A)
    profile = repo.profile(); profile.onboarding_status = 'complete'; profile.completed_attempts = 5
    profile.listening_profile.details = .69; repo.save_profile(profile)
    lesson = Lesson(id=str(uuid4()), started_at=now(), active_since=now(), name='Maya', focus='details')
    repo.create_lesson(lesson)
    before = repo.profile().model_dump(); checkpoint = repo.lesson().model_dump()
    other = b.get('/api/profile').json()['profile']
    response = a.put('/api/profile/accent-preferences', json={'accents': accents})
    assert response.status_code == 200
    expected = 'preferred' if accents else 'no_preference'
    assert response.json()['status'] == expected
    # New authenticated client/login restores the same account's preference.
    from test_auth import AccountClient
    restored = AccountClient(a.app, a.provider, 'valid-a').get('/api/profile').json()['profile']
    assert restored['accent_preferences']['accents'] == accents
    assert restored['accent_preferences']['status'] == expected
    assert b.get('/api/profile').json()['profile'] == other
    after = repo.profile().model_dump(); after['accent_preferences'] = before['accent_preferences']
    assert after == before and repo.lesson().model_dump() == checkpoint
    assert authorized_accent(repo.lesson()) is None
    assert offer(repo.profile().accent_preferences, [], {accent: object() for accent in accents}) == (accents[0] if accents else None)


@pytest.mark.parametrize('body', [{'accents': ['invalid']},
    {'accents': ['british'], 'learner_id': USER_A}, {'accents': ['british'], 'passage_accent': 'british'},
    {'accents': ['british'] * 18}, {'accents': 'british'}, {}])
def test_settings_schema_rejects_unowned_or_unsupported_fields(accounts, body):
    a, _, _ = accounts
    before = a.get('/api/profile').json()
    assert a.put('/api/profile/accent-preferences', json=body).status_code == 422
    assert a.get('/api/profile').json() == before


def test_settings_requires_authentication_and_origin_checks(accounts):
    a, _, _ = accounts
    anonymous = ASGIClient(a.app, a.provider)
    assert anonymous.put('/api/profile/accent-preferences', json={'accents': ['irish']}).status_code == 401
    assert a.put('/api/profile/accent-preferences', headers={'Authorization': 'Bearer invalid'}, json={'accents': []}).status_code == 401
    assert a.put('/api/profile/accent-preferences', headers={'Origin': 'https://evil.example'}, json={'accents': []}).status_code == 403
