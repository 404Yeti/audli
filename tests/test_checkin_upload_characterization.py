"""AUD-89 P2 (PR A): characterization of the check-in upload (POST /api/lessons/{id}/attempts) that the browser's reconciliation relies on.

No server behaviour changes here; these tests pin what the retry logic assumes, so a later change cannot silently invalidate it:
 * the revision check is the arbiter: a stale resend is refused with 409 BEFORE any speech-to-text call and changes nothing;
 * concurrent duplicates of one revision: exactly one commits, the other is a 409 with no provider call;
 * GET /api/lessons/{id} exposes revision, phase, status and pending, and reading it changes nothing;
 * a revision change alone proves nothing (end/resume bump it with no upload), and a pending transcription alone proves nothing
   (a resend at a fresh revision overwrites it and costs one more transcription);
 * a failed transcription (503) leaves revision and pending untouched, so it is a definitive "not delivered"."""
import asyncio
import re

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from conftest import ASGIClient, FakeProvider
from test_state_only_ready import dump, make_repo  # noqa: F401  (fixture and helper)

WALL = re.compile(r'"(elapsed_seconds|remaining_seconds|server_time)": ("[^"]*"|[0-9.]+)')


class CountingProvider(FakeProvider):
    transcriptions = 0
    failing = False

    async def transcribe(self, audio, filename):
        type(self).transcriptions += 1
        if type(self).failing:
            raise RuntimeError('provider outage')
        return await super().transcribe(audio, filename)


@pytest.fixture(autouse=True)
def reset_provider():
    CountingProvider.transcriptions = 0
    CountingProvider.failing = False


def build(make_repo, tmp_path):
    repo = make_repo('x')
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = CountingProvider()
    app = create_app(Settings(data_dir=tmp_path, _env_file=None), provider, repo)
    client = ASGIClient(app, provider)
    lesson = client.post('/api/lessons/start')
    assert lesson.status_code == 200 and lesson.json()['phase'] == 'WELCOME' and lesson.json()['pending'] is None
    return repo, app, client, lesson.json()


def upload(client, lesson_id, revision, audio):
    return client.post(f'/api/lessons/{lesson_id}/attempts', data={'revision': revision}, files={'audio': ('response.wav', audio, 'audio/wav')})


def state(client, lesson_id):
    return client.get(f'/api/lessons/{lesson_id}').json()


def stable(rows):
    return {name: [WALL.sub('', row) for row in table] for name, table in rows.items()}


def test_a_committed_upload_advances_the_revision_by_one_keeps_the_phase_and_sets_pending(make_repo, tmp_path, audio_bytes):
    _, _, client, lesson = build(make_repo, tmp_path)
    done = upload(client, lesson['id'], lesson['revision'], audio_bytes)
    assert done.status_code == 200 and CountingProvider.transcriptions == 1
    after = state(client, lesson['id'])
    assert after['revision'] == lesson['revision'] + 1 and after['phase'] == 'WELCOME' and after['status'] == 'active'
    assert after['pending']['text'] and done.json()['pending'] == after['pending']


def test_reading_the_lesson_exposes_what_reconciliation_needs_and_changes_nothing(make_repo, tmp_path, audio_bytes):
    repo, _, client, lesson = build(make_repo, tmp_path)
    upload(client, lesson['id'], lesson['revision'], audio_bytes)
    before = stable(dump(repo))
    first, second = state(client, lesson['id']), state(client, lesson['id'])
    assert {'id', 'revision', 'status', 'phase', 'pending'} <= set(first)
    for body in (first, second):
        for volatile in ('elapsed_seconds', 'remaining_seconds', 'server_time'):
            body.pop(volatile, None)
    assert first == second and stable(dump(repo)) == before


def test_a_stale_resend_after_the_commit_is_refused_before_any_transcription_and_changes_nothing(make_repo, tmp_path, audio_bytes):
    repo, _, client, lesson = build(make_repo, tmp_path)
    upload(client, lesson['id'], lesson['revision'], audio_bytes)
    before = stable(dump(repo))
    resend = upload(client, lesson['id'], lesson['revision'], audio_bytes)          # the browser lost the response and sends revision R again
    assert resend.status_code == 409 and 'Reload your current lesson checkpoint' in resend.json()['detail']
    assert CountingProvider.transcriptions == 1 and stable(dump(repo)) == before     # no provider call, no state change


def test_a_resend_at_the_fresh_revision_is_a_new_upload_it_overwrites_pending_and_costs_one_more_transcription(make_repo, tmp_path, audio_bytes):
    """Why a pending transcription is never treated as proof of delivery: re-sending is allowed, safe for state, but not free."""
    _, _, client, lesson = build(make_repo, tmp_path)
    upload(client, lesson['id'], lesson['revision'], audio_bytes)
    current = state(client, lesson['id'])
    again = upload(client, lesson['id'], current['revision'], audio_bytes)
    assert again.status_code == 200 and CountingProvider.transcriptions == 2
    after = state(client, lesson['id'])
    assert after['revision'] == current['revision'] + 1 and after['pending']['text'] == current['pending']['text']


def test_concurrent_duplicates_of_one_revision_commit_once_and_the_loser_is_a_409_without_a_provider_call(make_repo, tmp_path, audio_bytes):
    _, app, client, lesson = build(make_repo, tmp_path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*(http.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']},
                                                    files={'audio': ('response.wav', audio_bytes, 'audio/wav')}) for _ in range(4)))
    codes = sorted(response.status_code for response in asyncio.run(run()))
    assert codes == [200, 409, 409, 409] and CountingProvider.transcriptions == 1
    assert state(client, lesson['id'])['revision'] == lesson['revision'] + 1


def test_a_revision_change_alone_proves_nothing_end_and_resume_bump_it_with_no_upload(make_repo, tmp_path, audio_bytes):
    _, _, client, lesson = build(make_repo, tmp_path)
    assert client.post(f"/api/lessons/{lesson['id']}/end").status_code == 200
    resumed = client.post(f"/api/lessons/{lesson['id']}/resume")
    assert resumed.status_code == 200
    after = state(client, lesson['id'])
    assert after['revision'] > lesson['revision'] + 1                                  # moved by two (end, resume) ...
    assert after['pending'] is None and after['phase'] == lesson['phase'] and CountingProvider.transcriptions == 0   # ... and nothing was delivered
    # The old revision is now stale: a resend with it is refused, a resend with the fresh one works.
    assert upload(client, lesson['id'], lesson['revision'], audio_bytes).status_code == 409
    assert upload(client, lesson['id'], after['revision'], audio_bytes).status_code == 200


def test_an_upload_to_a_paused_lesson_is_refused_without_a_provider_call(make_repo, tmp_path, audio_bytes):
    _, _, client, lesson = build(make_repo, tmp_path)
    client.post(f"/api/lessons/{lesson['id']}/end")
    paused = state(client, lesson['id'])
    assert paused['status'] == 'paused'
    assert upload(client, lesson['id'], paused['revision'], audio_bytes).status_code == 409 and CountingProvider.transcriptions == 0


def test_a_failed_transcription_is_a_definitive_not_delivered_revision_and_pending_untouched(make_repo, tmp_path, audio_bytes):
    repo, _, client, lesson = build(make_repo, tmp_path)
    before = stable(dump(repo))
    CountingProvider.failing = True
    failed = upload(client, lesson['id'], lesson['revision'], audio_bytes)
    assert failed.status_code == 503 and CountingProvider.transcriptions == 1
    after = state(client, lesson['id'])
    assert after['revision'] == lesson['revision'] and after['pending'] is None and stable(dump(repo)) == before
    CountingProvider.failing = False
    assert upload(client, lesson['id'], lesson['revision'], audio_bytes).status_code == 200      # and the same revision can be used again

