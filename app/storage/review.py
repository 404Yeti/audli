"""Owned batch reads; no writes, providers, audio blobs, or intermediate judgments."""
from sqlalchemy import select, and_, exists
from app.storage import schema as s
from app.review import Entry, timestamp, chronological
from app.storage.repository import aware


def inventory(repo, db) -> list[Entry]:
    lessons = db.execute(select(s.lessons.c.id, s.lessons.c.status,
        s.lessons.c.data['completed_at'].as_string().label('completed_at'),
        s.lessons.c.data['plan_topic'].as_string().label('title'))
        .where(s.lessons.c.user_id == repo.learner_id)).mappings()
    entries = [Entry(row['id'], 'lesson', row['status'] == 'completed', timestamp(row['completed_at']), row['title']) for row in lessons]
    # Any link suppresses legacy counting, even if its lesson is incomplete.
    linked = exists(select(s.lesson_exercises.c.exercise_id).join(s.exercises,
        s.lesson_exercises.c.exercise_id == s.exercises.c.id).where(and_(
        s.exercises.c.session_id == s.sessions.c.id, s.exercises.c.user_id == repo.learner_id)))
    sessions = db.execute(select(s.sessions.c.id, s.sessions.c.completed_at).where(and_(
        s.sessions.c.user_id == repo.learner_id, s.sessions.c.status == 'completed', ~linked))).mappings()
    entries.extend(Entry(row['id'], 'earlier_practice', True, timestamp(aware(row['completed_at'])) if row['completed_at'] else None) for row in sessions)
    return chronological(entries)


def hydrate(repo, db, entries: list[Entry]):
    if not entries:
        return
    lesson_ids = [e.id for e in entries if e.kind == 'lesson']
    session_ids = [e.id for e in entries if e.kind == 'earlier_practice']
    owned_link = and_(s.lesson_exercises.c.exercise_id == s.exercises.c.id,
                      s.lesson_exercises.c.user_id == repo.learner_id)
    retained_audio = exists(select(s.audio_assets.c.audio_name).where(and_(
        s.audio_assets.c.audio_name == s.exercises.c.audio_name, s.audio_assets.c.user_id == repo.learner_id)))
    rows = db.execute(select(s.exercises.c.id.label('exercise_id'), s.exercises.c.session_id,
        s.lesson_exercises.c.lesson_id, s.exercises.c.content, s.exercises.c.difficulty,
        s.evaluations.c.data.label('evaluation'), s.events.c.data.label('adaptation'),
        retained_audio.label('audio_available'))
        .select_from(s.exercises.join(s.attempts, and_(s.attempts.c.exercise_id == s.exercises.c.id, s.attempts.c.status == 'evaluated'))
            .outerjoin(s.evaluations, s.evaluations.c.attempt_id == s.attempts.c.id)
            .outerjoin(s.events, s.events.c.attempt_id == s.attempts.c.id)
            .outerjoin(s.lesson_exercises, owned_link))
        .where(and_(s.exercises.c.user_id == repo.learner_id,
            (s.lesson_exercises.c.lesson_id.in_(lesson_ids)) |
            (s.exercises.c.session_id.in_(session_ids) & s.lesson_exercises.c.exercise_id.is_(None))))
        .order_by(s.exercises.c.created_at, s.exercises.c.id)).mappings()
    lookup = {entry.key: entry for entry in entries}
    for row in rows:
        key = f"lesson:{row['lesson_id']}" if row['lesson_id'] else f"earlier_practice:{row['session_id']}"
        if key in lookup:
            lookup[key].passages.append(dict(row))
