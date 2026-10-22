"""Authenticated, provider-free Review views over existing persistence."""
from typing import Annotated, Literal
from uuid import UUID
from fastapi import HTTPException, Query
from sqlalchemy import select
from app.storage import schema as s
from app.review import (ReviewDashboard, ReviewSummary, LessonReport, display_zone, lesson_card,
                        passage_report, listening_patterns, insight)
from app.storage.review import inventory, hydrate
from app.improvement import PROMPT_VERSION
from app.storage.improvement import live_rows, report_state
from app.storage.repository import utcnow


def register_review_routes(app, repo, settings=None):
    @app.get('/api/review', response_model=ReviewDashboard)
    async def dashboard(timezone: Annotated[str | None, Query(max_length=100)] = None,
                        cursor: Annotated[str | None, Query(max_length=100)] = None,
                        limit: Annotated[int, Query(ge=1, le=30)] = 12):
        zone, fallback = display_zone(timezone)
        with repo.engine.connect() as db:
            entries = inventory(repo, db)
            completed = [e for e in entries if e.completed]
            unfinished = [e for e in entries if not e.completed]
            offset = 0
            if cursor:
                offsets = [i + 1 for i, e in enumerate(completed) if e.key == cursor]
                if not offsets:
                    raise HTTPException(422, 'Review history changed. Reload your review.')
                offset = offsets[0]
            page = completed[offset:offset + limit]
            selected = {entry.key: entry for entry in [*page, *completed[:10], *unfinished]}
            hydrate(repo, db, list(selected.values()))
            profile = db.execute(select(s.profiles.c.data)
                .where(s.profiles.c.user_id == repo.learner_id)).scalar_one()
        patterns = listening_patterns(completed[:10], zone)
        dates = {entry.completed_at.astimezone(zone).date() for entry in completed if entry.completed_at}
        structured = sum(entry.kind == 'lesson' for entry in completed)
        level = (profile.get('difficulty') or {}).get('vocabulary_level') if profile.get('completed_attempts', 0) else None
        return ReviewDashboard(summary=ReviewSummary(completed_lessons=len(completed), structured_lessons=structured,
            earlier_practice=len(completed) - structured, practice_days=len(dates),
            undated_lessons=sum(entry.completed_at is None for entry in completed), timezone_used=zone.key,
            timezone_fallback=fallback, current_practice_level=level if level in ('A2', 'B1', 'B2', 'C1') else None),
            insight=insight(patterns), patterns=patterns, lessons=[lesson_card(e) for e in page],
            assessed_practice=[lesson_card(e) for e in unfinished if e.passages],
            next_cursor=page[-1].key if page and offset + limit < len(completed) else None)

    @app.get('/api/review/{kind}/{entry_id}', response_model=LessonReport)
    async def report(kind: Literal['lesson', 'earlier_practice'], entry_id: UUID):
        identifier = str(entry_id)
        with repo.engine.connect() as db:
            entry = next((entry for entry in inventory(repo, db, kind=kind, entry_id=identifier) if entry.kind == kind and entry.id == identifier), None)
            if entry is None:
                raise HTTPException(404, 'Lesson report not found.')
            hydrate(repo, db, [entry], answers=True)
            if not entry.completed and not entry.passages:
                raise HTTPException(404, 'Lesson report not found.')
            enabled = bool(settings and settings.review_improvement_enabled)
            if enabled:  # One owner-filtered batched read; never a provider call and never a write.
                stored = live_rows(repo, db, [row['exercise_id'] for row in entry.passages], PROMPT_VERSION)
                for row in entry.passages:
                    row['improvement_row'] = stored.get(row['exercise_id'])
        at = getattr(app.state, 'review_clock', utcnow)()
        return LessonReport(lesson=lesson_card(entry), passages=[passage_report(row,
            report_state(row, settings.min_transcription_confidence, at) if enabled else None) for row in entry.passages])
