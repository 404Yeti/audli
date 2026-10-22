"""Owned batch reads; no writes, providers, audio blobs, or intermediate judgments."""
from sqlalchemy import select, and_, exists
from app.storage import schema as s
from app.review import Entry, Kind, timestamp, chronological, text
from app.storage.repository import aware


def inventory(repo, db, *, kind: Kind | None = None, entry_id: str | None = None) -> list[Entry]:
    entries = []
    if kind != 'earlier_practice':
        query = select(s.lessons.c.id, s.lessons.c.status,
            s.lessons.c.data['completed_at'].as_string().label('completed_at'),
            s.lessons.c.data['plan_topic'].as_string().label('title')).where(s.lessons.c.user_id == repo.learner_id)
        if entry_id is not None:
            query = query.where(s.lessons.c.id == entry_id)
        lessons = db.execute(query).mappings()
        entries.extend(Entry(row['id'], 'lesson', row['status'] == 'completed',
            timestamp(row['completed_at']), text(row['title'])) for row in lessons)
    if kind != 'lesson':
        # An owned link suppresses legacy counting, including unfinished lessons.
        linked = exists(select(s.lesson_exercises.c.exercise_id).join(s.exercises,
            s.lesson_exercises.c.exercise_id == s.exercises.c.id).where(and_(
            s.exercises.c.session_id == s.sessions.c.id, s.exercises.c.user_id == repo.learner_id,
            s.lesson_exercises.c.user_id == repo.learner_id)))
        query = select(s.sessions.c.id, s.sessions.c.completed_at).where(and_(
            s.sessions.c.user_id == repo.learner_id, s.sessions.c.status == 'completed', ~linked))
        if entry_id is not None:
            query = query.where(s.sessions.c.id == entry_id)
        sessions = db.execute(query).mappings()
        entries.extend(Entry(row['id'], 'earlier_practice', True,
            timestamp(aware(row['completed_at'])) if row['completed_at'] else None) for row in sessions)
    return chronological(entries)


def hydrate(repo, db, entries: list[Entry], answers: bool = False):
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
        s.attempts.c.id.label('root_attempt'), s.attempts.c.transcription.label('root_transcription'),
        s.attempts.c.confirmed_text.label('root_text'), s.evaluations.c.data.label('evaluation'), s.events.c.data.label('adaptation'),
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
    if answers:
        attach_answers(repo, db, [row for entry in entries for row in entry.passages])


def uncertain(transcription) -> bool:
    return bool(isinstance(transcription, dict) and transcription.get('uncertainty'))


def attach_answers(repo, db, passages: list[dict]):
    """Saved responses per passage: accepted conversation turns, else the evaluated root attempt (older records).

    Every row joins back to an exercise owned by this learner. Only final, evaluated passages are hydrated."""
    ids = [row['exercise_id'] for row in passages]
    by_exercise: dict[str, list[dict]] = {exercise_id: [] for exercise_id in ids}
    if ids:
        turns = db.execute(select(s.turns.c.exercise_id, s.turns.c.attempt_id, s.turns.c.confirmed_text,
            s.followups.c.question, s.attempts.c.transcription)
            .select_from(s.turns.join(s.exercises, s.exercises.c.id == s.turns.c.exercise_id)
                .join(s.attempts, s.attempts.c.id == s.turns.c.attempt_id)
                .outerjoin(s.followups, s.followups.c.id == s.turns.c.followup_id))
            .where(and_(s.exercises.c.user_id == repo.learner_id, s.turns.c.exercise_id.in_(ids)))
            .order_by(s.turns.c.exercise_id, s.turns.c.position)).mappings()
        for turn in turns:
            by_exercise[turn['exercise_id']].append({'attempt_id': turn['attempt_id'], 'question': turn['question'],
                'text': turn['confirmed_text'], 'uncertain': uncertain(turn['transcription'])})
    for row in passages:
        saved = by_exercise[row['exercise_id']]
        if not saved and row.get('root_text'):
            saved = [{'attempt_id': row['root_attempt'], 'question': None, 'text': row['root_text'],
                      'uncertain': uncertain(row.get('root_transcription'))}]
        row['answers'] = saved
