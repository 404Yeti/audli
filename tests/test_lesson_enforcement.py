"""AUD-47 phase 3: wall-clock expiry, 429 contract, billable-endpoint gating, closing grace, failed-start void.

All time is a controlled server clock; the browser never supplies time. Mock providers establish plumbing only.
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.config import Settings
from app.lesson import Lesson
from app.lesson_limit import WINDOW
from app.limits import LessonLimits
from app.main import create_app
from app.onboarding_reset import reset_onboarding
from app.storage import schema as s
from app.storage.repository import aware
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory
from test_lesson_lifecycle import start, action, respond, exercises, complete_exercise

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, **delta):
        self.t += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    for target in ('app.lesson.now', 'app.lesson_api.now', 'app.limits.now', 'app.storage.repository.utcnow'):
        monkeypatch.setattr(target, clock)
    return clock


@pytest.fixture
def build(repository_factory, tmp_path, audio_bytes, clock):
    def make(mode='enforce', repo=None):
        repo = repo or repository_factory()
        profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
        provider = FakeProvider()
        provider.spoken, provider.failing = [], False
        provider.attempts = 0
        async def speak(text, speech_rate=.9):
            provider.attempts += 1
            if provider.failing:
                raise ValueError('Tutor speech temporarily unavailable')
            provider.spoken.append(text)
            return audio_bytes
        provider.speak = speak
        client = ASGIClient(create_app(Settings(data_dir=tmp_path, lesson_limit_mode=mode, _env_file=None), provider, repo), provider)
        client.repo = repo
        return client
    return make


def z(value):
    return value.isoformat().replace('+00:00', 'Z')


def ledger(client):
    with client.repo.engine.connect() as db:
        row = db.execute(select(s.lesson_entitlements).where(s.lesson_entitlements.c.user_id == client.repo.learner_id)).mappings().first()
    return row


def finish_closing(client, lesson):
    lesson = client.get(f"/api/lessons/{lesson['id']}").json()
    assert lesson['phase'] == 'CLOSING', lesson['phase']
    return action(client, lesson, 'heard')


def complete_lesson(client, audio_bytes):
    """A finished lesson at the current server time, written through the same checkpoint CAS the API uses."""
    lesson = start(client)
    stored = client.repo.lesson(lesson['id'])
    stored.status, stored.phase, stored.completed_at, stored.active_since = 'completed', 'COMPLETED', T0, None
    client.repo.save_lesson(stored, stored.revision)
    return client.get(f"/api/lessons/{lesson['id']}").json()


def refused(response, code, status=429):
    assert response.status_code == status, response.text
    body = response.json()
    assert body['error']['code'] == code and isinstance(body['detail'], str)
    if status == 429:
        assert int(response.headers['retry-after']) == body['error']['retry_after_seconds'] > 0
        assert body['error']['next_available_at'] and body['error']['server_time']
    return body['error']


def test_second_lesson_is_refused_with_structured_429_and_survives_refresh_and_devices(build, clock, audio_bytes):
    client, other_device = build(), None
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(hours=3)
    for _ in range(3):  # Refresh, retry, another tab or device: same answer, never a reset.
        error = refused(client.post('/api/lessons/start'), 'lesson_cooldown')
    assert error['next_available_at'] == z(T0 + WINDOW)
    assert error['retry_after_seconds'] == 21 * 3600
    assert client.get('/api/lessons/current').json() is None
    state = client.get('/api/eligibility').json()
    assert state['reason'] == 'cooldown' and state['enforced'] is True and state['eligible'] is False
    assert state['server_time'] == z(clock())
    clock.advance(hours=21, seconds=-1)
    refused(client.post('/api/lessons/start'), 'lesson_cooldown')
    clock.advance(seconds=1)
    assert client.post('/api/lessons/start').status_code == 200
    assert lesson['status'] == 'completed'


def test_pausing_never_stops_the_wall_clock_and_closing_uses_completed_work_only(build, clock, audio_bytes):
    client = build()
    lesson = exercises(client, audio_bytes)  # In an exercise, passage ready, nothing assessed yet.
    exercise = lesson['exercise']
    clock.advance(minutes=10, seconds=30)  # Past the deadline, inside the closing grace.
    # An already-active lesson cannot upload a new answer, even before the server settles it.
    spoken = len(client.provider.spoken)
    refused(client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('a.wav', audio_bytes, 'audio/wav')}), 'lesson_time_expired')
    paused = client.post(f"/api/lessons/{lesson['id']}/end").json()
    assert paused['status'] == 'paused' and paused['phase'] == 'CLOSING'
    state = client.get(f"/api/lessons/{lesson['id']}").json()
    assert state['phase'] == 'CLOSING' and state['status'] == 'paused' and state['exercise'] is None
    assert state['remaining_seconds'] == 0 and state['elapsed_seconds'] > 600
    assert client.post(f"/api/lessons/{lesson['id']}/exercise", json={'revision': state['revision']}).status_code == 409
    resumed = client.post(f"/api/lessons/{lesson['id']}/resume").json()
    assert resumed['status'] == 'active' and resumed['phase'] == 'CLOSING'
    closing = client.post(f"/api/lessons/{lesson['id']}/audio", json={'revision': resumed['revision']})
    assert closing.status_code == 200 and len(client.provider.spoken) == spoken + 1  # One closing speech.
    done = action(client, resumed, 'heard')
    assert done['status'] == 'completed' and done['exercises_completed'] == 0


def test_in_flight_assessment_finishes_then_feedback_and_advance_use_the_grace(build, clock, audio_bytes):
    client = build()
    lesson = exercises(client, audio_bytes)
    result = complete_exercise(client, lesson, audio_bytes)
    clock.advance(minutes=10, seconds=15)
    path = f"/api/exercises/{lesson['exercise']['id']}/coach-audio"
    feedback = client.post(path, json={'cue_id': 'feedback', 'lesson_id': lesson['id'], 'lesson_revision': client.get(f"/api/lessons/{lesson['id']}").json()['revision']})
    assert feedback.status_code == 200, feedback.text  # Final feedback is part of the closing sequence.
    current = client.get(f"/api/lessons/{lesson['id']}").json()
    assert current['phase'] == 'EXERCISE' and current['status'] == 'active'  # Not cut off before its feedback.
    closed = action(client, current, 'advance')
    assert closed['phase'] == 'CLOSING' and closed['exercise'] is None and result['state'] == 'GIVING_FEEDBACK'
    refused(client.post('/api/recognition/acknowledgment-audio', json={'cue': 'reflection'}), 'lesson_time_expired')


def test_no_followup_is_asked_after_the_deadline_and_unknown_stays_unknown(build, clock, audio_bytes):
    from test_conversation import scripted, answer as exercise_answer
    client = build()
    lesson = exercises(client, audio_bytes)
    exercise = lesson['exercise']
    scripted(client, [{dim: 'insufficient_evidence' for dim in ('main_idea', 'details', 'vocabulary', 'inference')}])
    assert client.post(f"/api/exercises/{exercise['id']}/conversation/listened").status_code == 200
    difficulty = client.get('/api/profile').json()['profile']['difficulty']
    clock.advance(minutes=9, seconds=59)
    # The answer was accepted before the deadline; the assessment lands after it.
    original = client.app.state.limits.admit_exercise
    def late(where, exercise_id):
        original(where, exercise_id)
        if where == 'turn.assess':
            clock.advance(seconds=5)  # Admitted just before the deadline; evaluation completes just after it.
    client.app.state.limits.admit_exercise = late
    _, _, result = exercise_answer(client, exercise, audio_bytes)
    body = result.json()
    assert body['state'] == 'GIVING_FEEDBACK' and body['followups_asked'] == 0  # Incomplete evidence, no extra turn.
    assert body['result']['adaptation']['new_difficulty'] == difficulty  # Difficulty stays stable.


def test_abandoned_lesson_expires_server_side_and_keeps_work_without_counting_as_completed(build, clock, audio_bytes):
    client = build()
    lesson = exercises(client, audio_bytes)
    complete_exercise(client, lesson, audio_bytes)  # Assessed evidence exists, the learner then wanders off.
    clock.advance(minutes=14)
    assert client.get('/api/lessons/current').json() is None
    stored = client.repo.lesson(lesson['id'])
    assert stored.status == 'expired' and stored.completed_at is None
    assert [item['exercise_id'] for item in client.repo.lesson_work(lesson['id'])] == [lesson['exercise']['id']]
    assert client.get(f"/api/exercises/{lesson['exercise']['id']}/transcript").status_code == 200  # Review stays open.
    assert client.get(f"/api/lessons/{lesson['id']}").json()['status'] == 'expired'
    assert client.get('/api/review').json()['summary']['completed_lessons'] == 0
    assert client.repo.lesson(completed=True) is None
    refused(client.post('/api/lessons/start'), 'lesson_cooldown')


def test_stale_session_cannot_use_an_expired_lesson(build, clock, audio_bytes):
    client = build()
    lesson = start(client)
    clock.advance(minutes=20)
    path = f"/api/lessons/{lesson['id']}"
    for verb in ('heard', 'audio', 'answer', 'exercise', 'advance', 'introduction-audio', 'introduction-heard'):
        assert client.post(f"{path}/{verb}", json={'revision': lesson['revision']}).status_code == 409, verb
    spoken = len(client.provider.spoken)
    assert client.post(f"{path}/attempts", data={'revision': lesson['revision']}, files={'audio': ('a.wav', audio_bytes, 'audio/wav')}).status_code == 409
    assert len(client.provider.spoken) == spoken
    assert client.repo.lesson(lesson['id']).status == 'expired'


def test_legacy_exercise_routes_are_unavailable_in_cooldown_and_without_a_lesson(build, clock, audio_bytes):
    client = build()
    refused(client.post('/api/exercises'), 'lesson_required', 409)  # Eligible but no lesson: start one.
    complete_lesson(client, audio_bytes)
    clock.advance(hours=1)
    generated = len(client.provider.spoken)
    refused(client.post('/api/exercises'), 'lesson_cooldown')
    assert len(client.provider.spoken) == generated
    assert client.get('/api/exercises/current').status_code == 200  # Reading stays available.
    assert client.get('/api/history').status_code == 200


def test_cache_only_review_never_generates_speech_or_calls_the_model(build, clock, audio_bytes):
    client = build()
    lesson = exercises(client, audio_bytes)
    complete_exercise(client, lesson, audio_bytes)
    exercise_id = lesson['exercise']['id']
    revision = client.get(f"/api/lessons/{lesson['id']}").json()['revision']
    cue = {'cue_id': 'feedback', 'lesson_id': lesson['id'], 'lesson_revision': revision}
    assert client.post(f"/api/exercises/{exercise_id}/coach-audio", json=cue).status_code == 200  # Generated once, inside the lesson.
    clock.advance(minutes=8)  # Inside the closing budget: the lesson now moves to its closing sequence.
    closing = action(client, client.get(f"/api/lessons/{lesson['id']}").json(), 'advance')
    assert closing['phase'] == 'CLOSING'
    assert client.post(f"/api/lessons/{lesson['id']}/audio", json={'revision': closing['revision']}).status_code == 200
    done = action(client, closing, 'heard')
    assert client.post(f"/api/lessons/{lesson['id']}/farewell-audio").status_code == 200
    clock.advance(days=2, hours=1)  # Cooldown is over, but this lesson is long finished.
    spoken = len(client.provider.spoken)
    assert client.post(f"/api/exercises/{exercise_id}/coach-audio", json={'cue_id': 'feedback'}).status_code == 200  # Saved copy.
    assert client.get(f"/api/exercises/{exercise_id}/coach-audio/feedback").status_code == 200
    assert client.get(f"/api/exercises/{exercise_id}/audio").status_code == 200
    assert client.post(f"/api/lessons/{lesson['id']}/farewell-audio").status_code == 200  # Cached.
    assert len(client.provider.spoken) == spoken and done['status'] == 'completed'
    # An uncached cue for the old exercise cannot be generated any more.
    with client.repo.engine.begin() as db:
        db.execute(s.coach_audio.delete())
    refused(client.post(f"/api/exercises/{exercise_id}/coach-audio", json={'cue_id': 'feedback'}), 'lesson_cooldown')
    assert len(client.provider.spoken) == spoken


def test_wrapup_speech_is_bounded_by_the_three_minute_grace(build, clock, audio_bytes):
    client = build()
    lesson = complete_lesson(client, audio_bytes)  # Completed at 0:00; wrap-up window ends 13:00.
    clock.advance(minutes=12)
    first = client.post(f"/api/lessons/{lesson['id']}/farewell-audio")
    assert first.status_code == 200
    count = len(client.provider.spoken)
    clock.advance(minutes=2)
    assert client.post(f"/api/lessons/{lesson['id']}/farewell-audio").status_code == 200  # Cached replay.
    assert len(client.provider.spoken) == count
    other = Lesson.model_validate({**client.repo.lesson(lesson['id']).model_dump(mode='json')})
    assert other.status == 'completed'


def test_uncached_wrapup_speech_after_the_grace_is_refused_and_makes_no_provider_call(build, clock, audio_bytes):
    client = build()
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(minutes=14)
    spoken = len(client.provider.spoken)
    refused(client.post(f"/api/lessons/{lesson['id']}/farewell-audio"), 'lesson_cooldown')
    assert len(client.provider.spoken) == spoken


def test_onboarding_restart_is_blocked_in_cooldown_but_existing_information_is_readable(build, clock, audio_bytes):
    client = build()
    complete_lesson(client, audio_bytes)
    clock.advance(hours=2)
    body = {'confirmation': 'RESTART ONBOARDING', 'revision': client.repo.profile().onboarding.revision}
    refused(client.post('/api/onboarding/restart', json=body), 'lesson_cooldown')
    controls = client.get('/api/onboarding/reset-controls').json()
    assert controls['restart_available'] is False and controls['restart_available_at'] == z(T0 + WINDOW)
    assert client.get('/api/onboarding').status_code == 200 and client.get('/api/profile').status_code == 200
    clock.advance(hours=23)
    assert client.post('/api/onboarding/restart', json=body).status_code == 200


def test_entitlement_survives_onboarding_reset_through_the_api(build, clock, audio_bytes):
    client = build()
    lesson = start(client)
    reset_onboarding(client.repo, client.repo.profile().onboarding.revision)  # Cleanup path that deletes open lessons.
    clock.advance(minutes=1)
    refused(client.post('/api/lessons/start'), 'lesson_cooldown')
    assert client.repo.lesson(lesson['id']) is None


def test_two_consecutive_first_speech_failures_void_the_allowance_narrowly(build, clock, audio_bytes):
    client = build()
    lesson = start(client)
    path = f"/api/lessons/{lesson['id']}/audio"
    client.provider.failing = True
    assert client.post(path, json={'revision': 0}).status_code == 503
    assert ledger(client)['first_speech_failures'] == 1 and ledger(client)['voided_at'] is None
    assert client.post(path, json={'revision': 0}).status_code == 503  # Second verified failure.
    row = ledger(client)
    assert row['voided_at'] is not None and aware(row['next_eligible_at']) == aware(row['last_started_at'])
    assert client.repo.lesson(lesson['id']).status == 'expired'
    client.provider.failing = False
    clock.advance(seconds=30)
    fresh = client.post('/api/lessons/start')
    assert fresh.status_code == 200 and fresh.json()['id'] != lesson['id']  # Allowance returned, no cooldown.
    assert client.post(f"/api/lessons/{fresh.json()['id']}/audio", json={'revision': 0}).status_code == 200
    clock.advance(minutes=1)
    stored = client.repo.lesson(fresh.json()['id'])
    stored.status, stored.phase, stored.completed_at, stored.active_since = 'completed', 'COMPLETED', clock(), None
    client.repo.save_lesson(stored, stored.revision)
    refused(client.post('/api/lessons/start'), 'lesson_cooldown')  # A void applies once; the new lesson counts.


def test_success_between_failures_resets_the_counter_and_abandonment_never_voids(build, clock, audio_bytes):
    client = build()
    lesson = start(client)
    path = f"/api/lessons/{lesson['id']}/audio"
    client.provider.failing = True
    assert client.post(path, json={'revision': 0}).status_code == 503
    client.provider.failing = False
    assert client.post(path, json={'revision': 0}).status_code == 200
    assert ledger(client)['first_speech_failures'] == 0
    client.provider.failing = True
    # Cached prompt: replay needs no provider, so a later outage cannot count against the learner.
    assert client.post(path, json={'revision': 0}).status_code == 200
    assert ledger(client)['voided_at'] is None
    clock.advance(minutes=14)  # Pure abandonment.
    assert client.get('/api/lessons/current').json() is None
    assert ledger(client)['voided_at'] is None
    refused(client.post('/api/lessons/start'), 'lesson_cooldown')


def test_progress_or_other_lessons_can_never_be_voided(build, clock, audio_bytes):
    client = build()
    lesson = start(client)
    assert client.repo.note_first_speech(lesson['id'], False) == 'counted'
    stored = client.repo.lesson(lesson['id'])
    client.repo.save_lesson(stored, stored.revision)  # Any progress: revision > 0.
    assert client.repo.note_first_speech(lesson['id'], False) == 'ignored'
    assert client.repo.note_first_speech(str(uuid4()), False) == 'ignored'
    assert ledger(client)['voided_at'] is None


def test_observe_logs_would_refuse_but_never_blocks_a_normal_learner(build, clock, audio_bytes, caplog):
    client = build('observe')
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    complete_lesson(client, audio_bytes)
    clock.advance(hours=1)
    assert client.post('/api/lessons/start').status_code == 200  # Would refuse; does not.
    assert client.get('/api/eligibility').json()['enforced'] is False
    limits = client.app.state.limits
    limits.admit('practice', 'probe')  # Open lesson, inside the window.
    clock.advance(minutes=30)
    limits.admit('practice', 'probe')  # Past the deadline: logged, not raised.
    assert client.get('/api/lessons/current').json()['status'] == 'active'  # Never expired by observe.
    assert client.repo.lesson(completed=True) is not None
    messages = [record.getMessage() for record in caplog.records if 'would_refuse' in record.getMessage()]
    assert any('lesson_cooldown' in m for m in messages) and any('lesson_time_expired' in m for m in messages)
    assert all(client.repo.learner_id not in m for m in messages)


def test_off_mode_preserves_current_behavior_exactly(build, clock, audio_bytes):
    client = build('off')
    complete_lesson(client, audio_bytes)
    assert client.post('/api/lessons/start').status_code == 200  # No allowance.
    clock.advance(hours=3)
    assert client.get('/api/lessons/current').json()['status'] == 'active'  # Active-time clock, no expiry.
    assert ledger(client) is None
    assert client.post('/api/exercises').status_code == 409  # Existing lesson-in-progress response, not a limit.


def test_clock_skew_only_the_server_clock_decides(build, clock, audio_bytes):
    client = build()
    complete_lesson(client, audio_bytes)
    skewed = {'Date': 'Mon, 01 Jan 2035 00:00:00 GMT', 'X-Client-Time': '2035-01-01T00:00:00Z'}
    clock.advance(hours=1)
    assert client.post('/api/lessons/start', headers=skewed).status_code == 429
    state = client.get('/api/eligibility', headers=skewed).json()
    assert state['server_time'] == z(clock()) and state['next_available_at'] == z(T0 + WINDOW)


def test_sqlite_development_file_from_before_expired_is_rebuilt_in_place(tmp_path):
    import sqlite3
    from app.repository import ProgressRepository
    path = tmp_path / 'legacy.sqlite3'
    repo = ProgressRepository(path, str(uuid4())); owner = repo.learner_id
    lesson = Lesson(id=str(uuid4()), started_at=T0, active_since=T0, name='Maya', focus='details')
    repo.create_lesson(lesson)
    repo.close()
    with sqlite3.connect(path) as raw:  # Recreate the pre-AUD-47 table definition.
        raw.execute('PRAGMA foreign_keys=OFF')
        ddl = raw.execute("SELECT sql FROM sqlite_master WHERE name='lesson_lifecycles'").fetchone()[0]
        raw.execute('ALTER TABLE lesson_lifecycles RENAME TO old')
        raw.execute('DROP INDEX one_open_lesson_per_learner')
        raw.execute(ddl.replace("'completed','expired'", "'completed'"))
        raw.execute('INSERT INTO lesson_lifecycles SELECT * FROM old'); raw.execute('DROP TABLE old')
        raw.execute("CREATE UNIQUE INDEX one_open_lesson_per_learner ON lesson_lifecycles (user_id) WHERE status != 'completed'")
    upgraded = ProgressRepository(path, owner)
    assert upgraded.lesson(lesson.id).id == lesson.id
    assert upgraded.expire_lesson(lesson.id, T0 + timedelta(minutes=14)).status == 'expired'
    assert upgraded.lesson() is None and upgraded.lesson(lesson.id).status == 'expired'
    upgraded.create_lesson(Lesson(id=str(uuid4()), started_at=T0, active_since=T0, name='Maya', focus='details'))
    upgraded.close()


def test_postgres_guard_allows_failure_counting_and_void_but_nothing_else(repository_factory):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL trigger')
    from sqlalchemy import text
    lesson = Lesson(id=str(uuid4()), started_at=T0, active_since=T0, name='Maya', focus='details')
    repo.create_lesson(lesson, limit_mode='enforce')
    assert repo.note_first_speech(lesson.id, False, T0) == 'counted'
    assert repo.note_first_speech(lesson.id, False, T0) == 'voided'  # Collapse is a permitted decrease.
    with pytest.raises(Exception, match='lesson_window_extension_refused'):
        with repo.engine.begin() as db:
            db.execute(text("UPDATE lesson_entitlements SET next_eligible_at = next_eligible_at + interval '1 day'"))


def test_closing_grace_cannot_become_unlimited_speech(build, clock, audio_bytes):
    from app.lesson_limit import GRACE_ACTION_LIMIT
    client = build()
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(minutes=11)  # After the deadline, inside the grace.
    limits, lesson_row = client.app.state.limits, client.repo.lesson(lesson['id'])
    for _ in range(GRACE_ACTION_LIMIT):
        limits.admit('wrapup', 'probe', lesson_row)
    from app.lesson_limit import LimitRefused
    with pytest.raises(LimitRefused):
        limits.admit('wrapup', 'probe', lesson_row)
    with pytest.raises(LimitRefused):
        limits.admit('practice', 'probe', lesson_row)  # Practice is never part of the grace.


def grace_count(client):
    return ledger(client)['grace_actions']


def test_cached_replay_consumes_no_grace_slot(build, clock, audio_bytes):
    client = build()
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(minutes=11)  # Inside the grace, after the deadline.
    assert client.post(f"/api/lessons/{lesson['id']}/farewell-audio").status_code == 200  # One generation, one slot.
    spoken = len(client.provider.spoken)
    assert grace_count(client) == 1
    for _ in range(25):  # Replays: cache hits never reach the gate.
        assert client.post(f"/api/lessons/{lesson['id']}/farewell-audio").status_code == 200
    assert grace_count(client) == 1 and len(client.provider.spoken) == spoken


def test_failed_provider_calls_consume_slots_so_retries_cannot_be_unlimited(build, clock, audio_bytes):
    from app.lesson_limit import GRACE_ACTION_LIMIT
    client = build()
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(minutes=11)
    client.provider.failing = True
    codes = [client.post(f"/api/lessons/{lesson['id']}/farewell-audio").status_code for _ in range(GRACE_ACTION_LIMIT + 5)]
    assert codes[:GRACE_ACTION_LIMIT] == [503] * GRACE_ACTION_LIMIT and codes[GRACE_ACTION_LIMIT:] == [429] * 5
    assert client.provider.attempts == GRACE_ACTION_LIMIT  # The provider was called exactly 12 times, never more.
    assert grace_count(client) == GRACE_ACTION_LIMIT


def test_cancelled_requests_keep_their_slot(build, clock, audio_bytes):
    client = build()
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(minutes=11)
    limits, stored = client.app.state.limits, client.repo.lesson(lesson['id'])
    try:
        limits.admit('wrapup', 'probe', stored)
        raise TimeoutError('the client went away before the provider answered')
    except TimeoutError:
        pass
    assert grace_count(client) == 1  # Admission is the charge; nothing refunds it.


def test_the_cap_holds_across_several_apps_over_one_database(build, clock, audio_bytes):
    from app.lesson_limit import GRACE_ACTION_LIMIT, LimitRefused
    first = build()
    second = build(repo=first.repo)  # A second worker/process with its own in-memory state.
    lesson = complete_lesson(first, audio_bytes)
    clock.advance(minutes=11)
    stored = first.repo.lesson(lesson['id'])
    admitted = 0
    for turn in range(GRACE_ACTION_LIMIT + 6):
        client = (first, second)[turn % 2]
        try:
            client.app.state.limits.admit('wrapup', 'probe', stored)
            admitted += 1
        except LimitRefused:
            pass
    assert admitted == GRACE_ACTION_LIMIT and grace_count(first) == GRACE_ACTION_LIMIT


def test_a_late_request_from_an_earlier_lesson_cannot_spend_the_next_lessons_grace(build, clock, audio_bytes):
    from app.lesson_limit import GRACE_ACTION_LIMIT, LimitRefused
    client = build()
    first = start(client)
    path = f"/api/lessons/{first['id']}/audio"
    client.provider.failing = True
    assert client.post(path, json={'revision': 0}).status_code == 503
    assert client.post(path, json={'revision': 0}).status_code == 503  # Verified failures: lesson expired, allowance returned.
    client.provider.failing = False
    earlier = client.repo.lesson(first['id'])
    assert earlier.status == 'expired'
    clock.advance(seconds=30)
    second = start(client)
    stored = client.repo.lesson(second['id'])
    clock.advance(minutes=10, seconds=30)  # The second lesson is now in its closing grace.
    for _ in range(4):  # Delayed requests that still carry the first lesson.
        with pytest.raises(LimitRefused):
            client.app.state.limits.admit('wrapup', 'late', earlier)
    assert grace_count(client) == 0
    for _ in range(GRACE_ACTION_LIMIT):  # The second lesson keeps its full allowance.
        client.app.state.limits.admit('wrapup', 'probe', stored)
    with pytest.raises(LimitRefused):
        client.app.state.limits.admit('wrapup', 'probe', stored)


def test_observe_never_takes_grace_slots(build, clock, audio_bytes):
    client = build('observe')
    lesson = complete_lesson(client, audio_bytes)
    clock.advance(minutes=11)
    stored = client.repo.lesson(lesson['id'])
    for _ in range(30):
        client.app.state.limits.admit('wrapup', 'probe', stored)  # Logs at most; never blocks or counts.
    assert grace_count(client) == 0


def test_displaying_generated_feedback_homework_and_success_never_consumes_a_slot(build, clock, audio_bytes):
    client = build()
    lesson = exercises(client, audio_bytes)
    complete_exercise(client, lesson, audio_bytes)
    exercise_id = lesson['exercise']['id']
    cue = {'cue_id': 'feedback', 'lesson_id': lesson['id'], 'lesson_revision': client.get(f"/api/lessons/{lesson['id']}").json()['revision']}
    assert client.post(f"/api/exercises/{exercise_id}/coach-audio", json=cue).status_code == 200  # Generated inside the 10 minutes.
    clock.advance(minutes=11)  # Deadline passed: only the closing sequence remains.
    before = grace_count(client)
    spoken = len(client.provider.spoken)
    for path in (f"/api/lessons/{lesson['id']}", f"/api/exercises/{exercise_id}/conversation", f"/api/exercises/{exercise_id}/coach-audio/feedback",
                 f"/api/exercises/{exercise_id}/transcript", f"/api/exercises/{exercise_id}/audio", '/api/homework', '/api/review',
                 '/api/history', '/api/eligibility', '/api/lessons/current', '/api/plan'):
        response = client.get(path)
        assert response.status_code in (200, 403, 404, 409, 503), (path, response.status_code)  # Never a limit refusal.
        assert response.status_code != 429, path
    assert grace_count(client) == before and len(client.provider.spoken) == spoken
    # Replaying the already-generated feedback cue is a cache hit: no slot, no provider call.
    assert client.post(f"/api/exercises/{exercise_id}/coach-audio", json={'cue_id': 'feedback'}).status_code == 200
    assert grace_count(client) == before and len(client.provider.spoken) == spoken


def test_only_provider_backed_closing_actions_take_slots(build, clock, audio_bytes):
    from app.lesson_limit import LimitRefused
    client = build()
    lesson = complete_lesson(client, audio_bytes)
    stored = client.repo.lesson(lesson['id'])
    limits = client.app.state.limits
    limits.admit('wrapup', 'early', stored)  # Before the deadline: the normal lesson, no ledger write.
    limits.admit('practice', 'early', stored) if stored.status in ('active', 'paused') else None
    assert grace_count(client) == 0
    clock.advance(minutes=11)
    with pytest.raises(LimitRefused):
        limits.admit('practice', 'practice-after-deadline', stored)  # Practice is never authorized in the grace.
    assert grace_count(client) == 0
    limits.admit('wrapup', 'closing-speech', stored)  # The authorized kind: one provider-backed closing action.
    assert grace_count(client) == 1
    clock.advance(minutes=3)  # Past the grace: refused outright, no slot is touched.
    with pytest.raises(LimitRefused):
        limits.admit('wrapup', 'too-late', stored)
    assert grace_count(client) == 1


def test_a_closing_speech_in_an_open_lesson_takes_a_slot_only_after_the_deadline(build, clock, audio_bytes):
    client = build()
    lesson = exercises(client, audio_bytes)
    complete_exercise(client, lesson, audio_bytes)
    clock.advance(minutes=8)
    closing = action(client, client.get(f"/api/lessons/{lesson['id']}").json(), 'advance')
    assert closing['phase'] == 'CLOSING'
    assert client.post(f"/api/lessons/{lesson['id']}/audio", json={'revision': closing['revision']}).status_code == 200
    assert grace_count(client) == 0  # Generated before the deadline: ordinary lesson speech, no grace slot.
