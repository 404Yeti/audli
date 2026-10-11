"""Explicit test-only operator activation; never used by application startup."""
from app.storage.accent_controls import SharedAccentControls
from app.lesson import Lesson, now
from app.accent_practice import resolve
from uuid import uuid4


def enable_test_controls(monkeypatch):
    original = SharedAccentControls.__init__
    def initialize(self, engine, settings):
        original(self, engine, settings)
        if settings.accent_practice_enabled:
            self.configure(enabled=True, daily_limit=settings.passage_tts_daily_characters,
                           concurrent_limit=settings.passage_tts_max_concurrent)
    monkeypatch.setattr(SharedAccentControls, '__init__', initialize)


def scope(repo, settings, accent='british'):
    profile = repo.profile()
    lesson = Lesson(id=str(uuid4()), name=profile.name, focus=profile.focus, started_at=now(), active_since=now())
    resolve(lesson, f'Practice {accent}', settings.passage_voices)
    lesson.phase = 'EXERCISE'
    repo.create_lesson(lesson)
    return repo.lesson()
