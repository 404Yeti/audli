"""AUD-82 incident (2026-10-24 10:20 UTC): a lesson whose learner still had an unfinished exercise from an earlier, ended lesson
could never get its next listening passage: the old exercise was re-attached (lesson_exercises.exercise_id is the primary key)
and every retry failed with the same UniqueViolation, shown to the learner as a generic 503."""
import io
import math
import struct
import wave

import pytest
from sqlalchemy import select, func

from app.config import Settings
from app.main import create_app
from app.storage import schema as s
from conftest import ASGIClient, FakeProvider
from test_state_only_ready import make_repo  # noqa: F401  (fixture)


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
        handle.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(24000)))
    return out.getvalue()


class Harness:
    def __init__(self, make_repo, tmp_path, label):
        self.repo = make_repo(label)
        profile = self.repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; self.repo.save_profile(profile)
        self.provider = FakeProvider()
        self.audio = wav()
        self.generated = 0
        original = self.provider.generate

        async def generate(profile):
            self.generated += 1
            return await original(profile)

        async def speak(text, speech_rate=.9):
            return self.audio
        self.provider.generate, self.provider.speak = generate, speak
        self.client = ASGIClient(create_app(Settings(data_dir=tmp_path, _env_file=None), self.provider, self.repo), self.provider)

    def post(self, path, **kw):
        response = self.client.post(path, **kw)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])
        return response.json()

    def step(self, lesson, verb):
        return self.post(f"/api/lessons/{lesson['id']}/{verb}", json={'revision': lesson['revision']})

    def to_exercise_phase(self):
        lesson = self.post('/api/lessons/start')
        uploaded = self.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']}, files={'audio': ('r.wav', self.audio, 'audio/wav')})
        lesson = self.step(uploaded, 'answer')
        lesson = self.step(lesson, 'heard')
        if lesson['phase'] == 'REVIEW':
            uploaded = self.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']}, files={'audio': ('r.wav', self.audio, 'audio/wav')})
            lesson = self.step(self.step(uploaded, 'answer'), 'heard')
        return self.step(lesson, 'heard')

    def expire(self, lesson_id):
        lesson = self.repo.lesson(lesson_id)
        lesson.status = 'expired'
        self.repo.save_lesson(lesson, lesson.revision)

    def rows(self, table):
        with self.repo.engine.connect() as db:
            return db.execute(select(func.count()).select_from(table)).scalar_one()


def test_a_new_lesson_resumes_the_unfinished_exercise_an_ended_lesson_left_behind(make_repo, tmp_path):
    h = Harness(make_repo, tmp_path, 'stale')
    first = h.to_exercise_phase()
    old_exercise = h.step(first, 'exercise')['exercise']['id']
    assert h.generated == 1
    h.expire(first['id'])                                           # the first lesson ended before the exercise was completed
    second = h.to_exercise_phase()
    assert second['id'] != first['id'] and second['phase'] == 'EXERCISE'
    result = h.post(f"/api/lessons/{second['id']}/exercise", json={'revision': second['revision']})      # was: 503 UniqueViolation, forever
    assert result['exercise']['id'] == old_exercise and h.generated == 1        # the unfinished exercise is resumed, no new passage is bought
    assert h.rows(s.lesson_exercises) == 1                                       # still one link per exercise: moved, not duplicated
    assert h.repo.lesson_for_exercise(old_exercise).id == second['id']
    assert h.repo.lesson(second['id']).exercise_id == old_exercise
    assert not h.repo.completed(old_exercise)                                    # nothing about the exercise itself was altered
    assert h.repo.lesson(first['id']).status == 'expired'                         # and the old lesson is untouched


def test_the_next_passage_request_is_idempotent_for_the_lesson(make_repo, tmp_path):
    h = Harness(make_repo, tmp_path, 'idem')
    first = h.to_exercise_phase()
    h.step(first, 'exercise')
    h.expire(first['id'])
    second = h.to_exercise_phase()
    one = h.post(f"/api/lessons/{second['id']}/exercise", json={'revision': second['revision']})
    two = h.post(f"/api/lessons/{second['id']}/exercise", json={'revision': one['revision']})
    assert one['exercise']['id'] == two['exercise']['id'] and h.rows(s.lesson_exercises) == 1 and h.generated == 1


def test_an_exercise_is_never_taken_from_a_lesson_that_is_still_open(make_repo, tmp_path):
    h = Harness(make_repo, tmp_path, 'open')
    first = h.to_exercise_phase()
    exercise = h.step(first, 'exercise')['exercise']['id']
    other = h.repo.lesson(first['id']).model_copy(deep=True)
    other.id, other.exercise_id = '00000000-0000-4000-8000-000000000777', None
    with pytest.raises(ValueError):                                                # attaching to a different lesson while the owner is open
        h.repo.attach_lesson_exercise(other, exercise)
    assert h.repo.lesson_for_exercise(exercise).id == first['id']


def test_an_exercise_that_belongs_to_no_lesson_is_still_reused_without_generating(make_repo, tmp_path):
    h = Harness(make_repo, tmp_path, 'unattached')
    legacy = h.post('/api/exercises')                                  # lesson-free exercise, attached to nothing
    assert h.generated == 1 and h.rows(s.lesson_exercises) == 0
    lesson = h.to_exercise_phase()
    result = h.step(lesson, 'exercise')
    assert result['exercise']['id'] == legacy['id'] and h.generated == 1  # reused and attached, as before
    assert h.repo.lesson_for_exercise(legacy['id']).id == lesson['id']


def test_lesson_free_use_keeps_returning_the_unfinished_exercise(make_repo, tmp_path):
    h = Harness(make_repo, tmp_path, 'legacy')
    one = h.post('/api/exercises')
    again = h.post('/api/exercises')
    assert one['id'] == again['id'] and h.generated == 1
