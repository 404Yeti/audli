"""Persistence for 2.0 listening lessons. Plain functions over a SQLAlchemy connection; callers own the transaction.

Every learner-scoped function takes the verified ``user_id`` and filters on it, so a guessed identifier from another account
is indistinguishable from a missing row. Answer keys are read only by ``submit_answer`` and, after an answer exists, by
``attempt_view`` for that one answered exercise.
"""
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select, func, and_, update, insert, case

from app.listening import schema as t
from app.listening.domain import LessonContent, Cue, validate_alignment, score_multiple_choice, AnswerKey
from app.storage.repository import utcnow


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


@dataclass(frozen=True)
class AudioFile:
    accent: str
    voice: str
    provider: str
    object_key: str
    content_type: str
    sha256: str
    size_bytes: int
    duration_ms: int
    cues: list[Cue]


def _add_audio(db, version_id: str, sentence_count: int, audio: AudioFile) -> str:
    validate_alignment(audio.cues, sentence_count, audio.duration_ms)
    existing = db.execute(select(t.audio.c.id, t.audio.c.sha256).where(and_(
        t.audio.c.version_id == version_id, t.audio.c.accent == audio.accent, t.audio.c.voice == audio.voice))).first()
    if existing:
        if existing.sha256 != audio.sha256:
            raise Conflict('This audio variant already exists with different content; publish a new lesson version instead.')
        return existing.id
    shared = db.execute(select(t.audio.c.sha256).where(t.audio.c.object_key == audio.object_key).limit(1)).scalar_one_or_none()
    if shared is not None and shared != audio.sha256:
        raise Conflict('That storage key already holds different audio.')
    variant_id = str(uuid4())
    db.execute(insert(t.audio).values(id=variant_id, version_id=version_id, accent=audio.accent, voice=audio.voice,
        provider=audio.provider, object_key=audio.object_key, content_type=audio.content_type, sha256=audio.sha256,
        size_bytes=audio.size_bytes, duration_ms=audio.duration_ms, alignment=[c.model_dump() for c in audio.cues], created_at=utcnow()))
    return variant_id


def publish_version(db, content: LessonContent, audio: AudioFile) -> tuple[str, bool]:
    """Publish content as an immutable version (idempotent by content hash). Returns (version_id, created)."""
    digest = content.content_hash()
    lesson = db.execute(select(t.lessons.c.id).where(t.lessons.c.slug == content.slug)).scalar_one_or_none()
    if lesson is None:
        lesson = str(uuid4())
        db.execute(insert(t.lessons).values(id=lesson, slug=content.slug, created_at=utcnow()))
    existing = db.execute(select(t.versions.c.id).where(and_(t.versions.c.lesson_id == lesson, t.versions.c.content_hash == digest))).scalar_one_or_none()
    if existing:
        _add_audio(db, existing, len(content.sentences), audio)
        return existing, False
    number = (db.execute(select(func.max(t.versions.c.version_no)).where(t.versions.c.lesson_id == lesson)).scalar() or 0) + 1
    version_id, exercise_id = str(uuid4()), str(uuid4())
    db.execute(insert(t.versions).values(id=version_id, lesson_id=lesson, version_no=number, status='draft', content_hash=digest,
        title=content.title, topic=content.topic, language=content.language, cefr=content.cefr, listen_for=content.listen_for,
        speakers=[s.model_dump() for s in content.speakers], sentences=[s.model_dump() for s in content.sentences],
        created_at=utcnow(), published_at=None))
    db.execute(insert(t.exercises).values(id=exercise_id, version_id=version_id, position=1, kind='multiple_choice',
        prompt=content.exercise.prompt, options=[o.model_dump() for o in content.exercise.options]))
    db.execute(insert(t.keys).values(exercise_id=exercise_id, correct_option_id=content.key.correct_option_id, explanation=content.key.explanation))
    _add_audio(db, version_id, len(content.sentences), audio)
    db.execute(update(t.versions).where(t.versions.c.id == version_id).values(status='published', published_at=utcnow()))
    return version_id, True


# ---- learner-safe reads ----------------------------------------------------------------------------------------------

def _latest_published(lesson_id: str):
    return (select(t.versions).where(and_(t.versions.c.lesson_id == lesson_id, t.versions.c.status == 'published'))
            .order_by(t.versions.c.version_no.desc()).limit(1))


def list_catalog(db) -> list[dict]:
    latest = (select(t.versions.c.lesson_id, func.max(t.versions.c.version_no).label('n')).where(t.versions.c.status == 'published')
              .group_by(t.versions.c.lesson_id).subquery())
    rows = db.execute(select(t.lessons.c.id.label('lesson_id'), t.lessons.c.slug, t.versions.c.id.label('version_id'), t.versions.c.title,
        t.versions.c.topic, t.versions.c.cefr, t.versions.c.listen_for)
        .join(latest, latest.c.lesson_id == t.lessons.c.id)
        .join(t.versions, and_(t.versions.c.lesson_id == latest.c.lesson_id, t.versions.c.version_no == latest.c.n))
        .order_by(t.versions.c.title)).mappings().all()
    result = []
    for row in rows:
        variants = db.execute(select(t.audio.c.accent, t.audio.c.duration_ms).where(t.audio.c.version_id == row['version_id'])
                              .order_by(t.audio.c.accent)).all()
        result.append({**row, 'accents': [v.accent for v in variants], 'duration_ms': max((v.duration_ms for v in variants), default=0)})
    return result


def _public_audio(db, version_id: str) -> list[dict]:
    rows = db.execute(select(t.audio.c.id, t.audio.c.accent, t.audio.c.voice, t.audio.c.duration_ms, t.audio.c.alignment)
                      .where(t.audio.c.version_id == version_id).order_by(t.audio.c.accent, t.audio.c.voice)).mappings().all()
    return [dict(r) for r in rows]


def lesson_detail(db, lesson_id: str) -> dict:
    version = db.execute(_latest_published(lesson_id)).mappings().first()
    if not version:
        raise NotFound('Lesson not found.')
    return {'lesson_id': lesson_id, 'version_id': version['id'], 'version_no': version['version_no'],
            **{k: version[k] for k in ('title', 'topic', 'cefr', 'listen_for', 'speakers', 'sentences')},
            'audio': _public_audio(db, version['id'])}


def _owned_attempt(db, user_id: str, attempt_id: str, lock: bool = False):
    query = select(t.attempts).where(and_(t.attempts.c.id == attempt_id, t.attempts.c.user_id == user_id))
    row = db.execute(query.with_for_update() if lock else query).mappings().first()
    if not row:
        raise NotFound('Attempt not found.')
    return row


def attempt_view(db, user_id: str, attempt_id: str) -> dict:
    attempt = _owned_attempt(db, user_id, attempt_id)
    version = db.execute(select(t.versions).where(t.versions.c.id == attempt['version_id'])).mappings().one()
    questions = db.execute(select(t.exercises.c.id, t.exercises.c.position, t.exercises.c.kind, t.exercises.c.prompt, t.exercises.c.options)
                           .where(t.exercises.c.version_id == version['id']).order_by(t.exercises.c.position)).mappings().all()
    answered = {r['exercise_id']: r for r in db.execute(
        select(t.responses.c.exercise_id, t.responses.c.option_id, t.responses.c.is_correct, t.keys.c.correct_option_id, t.keys.c.explanation)
        .join(t.keys, t.keys.c.exercise_id == t.responses.c.exercise_id)
        .where(t.responses.c.attempt_id == attempt_id)).mappings().all()}
    audio = next((a for a in _public_audio(db, version['id']) if a['id'] == attempt['audio_variant_id']), None)
    return {
        'attempt': {'id': attempt['id'], 'status': attempt['status'], 'total': attempt['total'], 'correct': attempt['correct'],
                    'created_at': attempt['created_at'], 'completed_at': attempt['completed_at']},
        'lesson': {'lesson_id': version['lesson_id'], 'version_id': version['id'], 'version_no': version['version_no'],
                   **{k: version[k] for k in ('title', 'topic', 'cefr', 'listen_for', 'speakers', 'sentences')}},
        'audio': audio,
        'exercises': [{**q, 'feedback': ({'selected_option_id': answered[q['id']]['option_id'], 'is_correct': answered[q['id']]['is_correct'],
                                          'correct_option_id': answered[q['id']]['correct_option_id'], 'explanation': answered[q['id']]['explanation']}
                                         if q['id'] in answered else None)} for q in questions],
    }


def attempt_audio_object(db, user_id: str, attempt_id: str) -> dict:
    attempt = _owned_attempt(db, user_id, attempt_id)
    row = db.execute(select(t.audio.c.object_key, t.audio.c.content_type, t.audio.c.duration_ms).where(t.audio.c.id == attempt['audio_variant_id'])).mappings().one()
    return dict(row)


def current_attempt_id(db, user_id: str, lesson_id: str) -> str | None:
    """The learner's open attempt for this lesson, else their most recent finished one."""
    rows = db.execute(select(t.attempts.c.id, t.attempts.c.status).join(t.versions, t.versions.c.id == t.attempts.c.version_id)
        .where(and_(t.attempts.c.user_id == user_id, t.versions.c.lesson_id == lesson_id))
        .order_by((t.attempts.c.status == 'in_progress').desc(), t.attempts.c.created_at.desc()).limit(1)).first()
    return rows.id if rows else None


# ---- learner writes --------------------------------------------------------------------------------------------------

def create_attempt(db, user_id: str, lesson_id: str, client_request_id: str, accent: str | None) -> tuple[str, bool]:
    """Idempotent. The same request id returns the same attempt; an open attempt for the lesson is resumed, never duplicated."""
    prior = db.execute(select(t.attempts.c.id, t.versions.c.lesson_id).join(t.versions, t.versions.c.id == t.attempts.c.version_id)
        .where(and_(t.attempts.c.user_id == user_id, t.attempts.c.client_request_id == client_request_id))).first()
    if prior:
        if prior.lesson_id != lesson_id:
            raise Conflict('This request id was already used for a different lesson.')
        return prior.id, False
    version = db.execute(_latest_published(lesson_id)).mappings().first()
    if not version:
        raise NotFound('Lesson not found.')
    open_attempt = db.execute(select(t.attempts.c.id).where(and_(t.attempts.c.user_id == user_id, t.attempts.c.version_id == version['id'],
                                                                 t.attempts.c.status == 'in_progress'))).scalar_one_or_none()
    if open_attempt:
        return open_attempt, False
    variants = db.execute(select(t.audio.c.id, t.audio.c.accent).where(t.audio.c.version_id == version['id']).order_by(t.audio.c.accent, t.audio.c.voice)).all()
    chosen = next((v for v in variants if accent is None or v.accent == accent), None)
    if chosen is None:
        raise Invalid('That accent is not available for this lesson.' if variants else 'This lesson has no audio yet.')
    total = db.execute(select(func.count()).select_from(t.exercises).where(t.exercises.c.version_id == version['id'])).scalar_one()
    attempt_id = str(uuid4())
    db.execute(insert(t.attempts).values(id=attempt_id, user_id=user_id, version_id=version['id'], audio_variant_id=chosen.id,
        client_request_id=client_request_id, status='in_progress', total=total, correct=None, created_at=utcnow(), completed_at=None))
    return attempt_id, True


def submit_answer(db, user_id: str, attempt_id: str, exercise_id: str, option_id: str) -> bool:
    """Record one answer exactly once. Returns True for a replay of the identical answer; a different second answer is refused."""
    attempt = _owned_attempt(db, user_id, attempt_id, lock=True)
    exercise = db.execute(select(t.exercises.c.id, t.exercises.c.options).where(and_(
        t.exercises.c.id == exercise_id, t.exercises.c.version_id == attempt['version_id']))).mappings().first()
    if not exercise:
        raise NotFound('Question not found in this attempt.')
    if option_id not in {o['id'] for o in exercise['options']}:
        raise Invalid('Choose one of the listed answers.')
    existing = db.execute(select(t.responses.c.option_id).where(and_(t.responses.c.attempt_id == attempt_id, t.responses.c.exercise_id == exercise_id))).scalar_one_or_none()
    if existing is not None:
        if existing == option_id:
            return True
        raise Conflict('This question was already answered.')
    if attempt['status'] != 'in_progress':
        raise Conflict('This attempt is already complete.')
    key = db.execute(select(t.keys.c.correct_option_id, t.keys.c.explanation).where(t.keys.c.exercise_id == exercise_id)).mappings().one()
    correct = score_multiple_choice(AnswerKey(**key), option_id)
    db.execute(insert(t.responses).values(attempt_id=attempt_id, exercise_id=exercise_id, version_id=attempt['version_id'],
        option_id=option_id, is_correct=correct, submitted_at=utcnow()))
    done = db.execute(select(func.count(), func.coalesce(func.sum(case((t.responses.c.is_correct, 1), else_=0)), 0))
                      .where(t.responses.c.attempt_id == attempt_id)).first()
    if done[0] >= attempt['total']:
        db.execute(update(t.attempts).where(t.attempts.c.id == attempt_id).values(status='completed', correct=int(done[1]), completed_at=utcnow()))
    return False
