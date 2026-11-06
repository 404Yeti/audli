"""Persistence for 2.0 listening lessons. Plain functions over a SQLAlchemy connection; callers own the transaction.

Every learner-scoped function takes the verified ``user_id`` and filters on it, so a guessed identifier from another account
is indistinguishable from a missing row. Answer keys are read only by ``submit_answer`` and, after an answer exists, by
``attempt_view`` for that one answered exercise.
"""
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select, func, and_, or_, update, insert, case

from app.listening import schema as t
from app.listening.domain import (LessonContent, Cue, validate_alignment, score_multiple_choice, AnswerKey, FillKey, SequenceKey, score_fill_blank,
                                  score_sequence, sequence_positions_correct, normalize_text)
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


def _extra_exercises(content: LessonContent):
    """Milestone 02 exercises in journey order (positions 2-4). Their keys are stored as private rubrics, never as exercise options."""
    out = []
    if content.spoken:
        out.append((2, 'spoken', content.spoken.prompt, [], {'points': [p.model_dump() for p in content.spoken.points]}))
    if content.fill_blank:
        out.append((len(out) + 2, 'fill_blank', content.fill_blank.prompt, [],
                    {'accepted': content.fill_key.accepted, 'explanation': content.fill_key.explanation}))
    if content.sequencing:
        out.append((len(out) + 2, 'sequencing', content.sequencing.prompt, [i.model_dump() for i in content.sequencing.items],
                    {'order': content.sequence_key.order, 'explanation': content.sequence_key.explanation}))
    return out


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
    for position, kind, prompt, options, rubric in _extra_exercises(content):
        extra_id = str(uuid4())
        db.execute(insert(t.exercises).values(id=extra_id, version_id=version_id, position=position, kind=kind, prompt=prompt, options=options))
        db.execute(insert(t.rubrics).values(exercise_id=extra_id, data=rubric))
    _add_audio(db, version_id, len(content.sentences), audio)
    db.execute(update(t.versions).where(t.versions.c.id == version_id).values(status='published', published_at=utcnow()))
    return version_id, True


# ---- learner-safe reads ----------------------------------------------------------------------------------------------

def _visible(user_id: str | None):
    """Shared-library lessons (no owner row) are visible to every learner; a generated lesson only to the learner who made it. No learner: shared only."""
    owned = select(t.lesson_owners.c.lesson_id).where(t.lesson_owners.c.lesson_id == t.lessons.c.id)
    if user_id is None:
        return ~owned.exists()
    return or_(~owned.exists(), owned.where(t.lesson_owners.c.user_id == user_id).exists())


def lesson_visible(db, user_id: str | None, lesson_id: str) -> bool:
    return db.execute(select(t.lessons.c.id).where(and_(t.lessons.c.id == lesson_id, _visible(user_id)))).first() is not None


def _latest_published(lesson_id: str):
    return (select(t.versions).where(and_(t.versions.c.lesson_id == lesson_id, t.versions.c.status == 'published'))
            .order_by(t.versions.c.version_no.desc()).limit(1))


def _decided_counts(db, attempt_ids: list[str]) -> dict[str, int]:
    """Questions the learner has answered (or skipped, or sent a recording for) in each attempt."""
    counts = {i: 0 for i in attempt_ids}
    if not attempt_ids:
        return counts
    for table, extra in ((t.responses, None), (t.answers, None), (t.spoken, t.spoken.c.status.in_(('skipped', 'pending', 'assessed', 'failed')))):
        query = select(table.c.attempt_id, func.count()).where(table.c.attempt_id.in_(attempt_ids)).group_by(table.c.attempt_id)
        for attempt_id, n in db.execute(query if extra is None else query.where(extra)).all():
            counts[attempt_id] += n
    return counts


def _scored_totals(db, version_ids: list[str]) -> dict[str, int]:
    """How many questions per lesson version are scored by application code (everything except the spoken answer)."""
    if not version_ids:
        return {}
    rows = db.execute(select(t.exercises.c.version_id, func.count()).where(and_(t.exercises.c.version_id.in_(version_ids), t.exercises.c.kind != 'spoken'))
                      .group_by(t.exercises.c.version_id)).all()
    return {v: n for v, n in rows}


def _spoken_states(db, attempt_ids: list[str]) -> dict[str, str]:
    if not attempt_ids:
        return {}
    return {a: s for a, s in db.execute(select(t.spoken.c.attempt_id, t.spoken.c.status).where(
        and_(t.spoken.c.attempt_id.in_(attempt_ids), t.spoken.c.status != 'receiving'))).all()}


_LIBRARY_TABLE_SEEN = False


def lesson_format(speakers) -> str:
    """story = one speaker; dialogue = two speakers sharing an accent (or none recorded); mixed = two speakers with different accents."""
    people = [s for s in (speakers or []) if isinstance(s, dict)]
    if len(people) < 2:
        return 'story'
    accents = {s.get('accent') for s in people if s.get('accent')}
    return 'mixed' if len(accents) > 1 else 'dialogue'


def library_categories(db) -> dict[str, str]:
    """lesson_id -> curated category, when migration 014 is applied; {} when it is not (nothing else reads that table, so an unmigrated deployment is unaffected)."""
    global _LIBRARY_TABLE_SEEN
    if not _LIBRARY_TABLE_SEEN:
        from sqlalchemy import inspect as sa_inspect
        if not sa_inspect(db).has_table(t.library_entries.name):
            return {}
        _LIBRARY_TABLE_SEEN = True
    return {r.lesson_id: r.category for r in db.execute(select(t.library_entries.c.lesson_id, t.library_entries.c.category))}


def list_catalog(db, user_id: str | None = None) -> list[dict]:
    """Published lessons (latest version each). With a learner id, each lesson also says where that learner is with it."""
    latest = (select(t.versions.c.lesson_id, func.max(t.versions.c.version_no).label('n')).where(t.versions.c.status == 'published')
              .group_by(t.versions.c.lesson_id).subquery())
    rows = db.execute(select(t.lessons.c.id.label('lesson_id'), t.lessons.c.slug, t.versions.c.id.label('version_id'), t.versions.c.title,
        t.versions.c.topic, t.versions.c.cefr, t.versions.c.listen_for, t.versions.c.speakers)
        .join(latest, latest.c.lesson_id == t.lessons.c.id)
        .join(t.versions, and_(t.versions.c.lesson_id == latest.c.lesson_id, t.versions.c.version_no == latest.c.n))
        .where(_visible(user_id))
        .order_by(t.versions.c.cefr, t.versions.c.title)).mappings().all()
    result = []
    categories = library_categories(db)
    for row in rows:
        row = dict(row)
        speakers = row.pop('speakers', None) or []
        variants = db.execute(select(t.audio.c.accent, t.audio.c.duration_ms).where(t.audio.c.version_id == row['version_id'])
                              .order_by(t.audio.c.accent)).all()
        # A mixed-accent lesson lists every speaker's accent (so the library and filters tell the truth); older lessons carry none and fall back to the audio's accent.
        spoken = [a for a in dict.fromkeys(s.get('accent') for s in speakers if isinstance(s, dict)) if a]
        result.append({**row, 'accents': spoken or [v.accent for v in variants], 'duration_ms': max((v.duration_ms for v in variants), default=0),
                       'format': lesson_format(speakers), 'category': categories.get(row['lesson_id'])})
    if user_id is not None:
        _add_progress(db, user_id, result)
    return result


def _add_progress(db, user_id: str, items: list[dict]) -> None:
    mine = db.execute(select(t.attempts.c.id, t.attempts.c.status, t.attempts.c.version_id, t.attempts.c.total, t.attempts.c.correct,
                             t.attempts.c.created_at, t.attempts.c.completed_at, t.versions.c.lesson_id)
                      .join(t.versions, t.versions.c.id == t.attempts.c.version_id).where(t.attempts.c.user_id == user_id)
                      .order_by(t.attempts.c.created_at.desc())).mappings().all()
    by_lesson: dict[str, list] = {}
    for attempt in mine:
        by_lesson.setdefault(attempt['lesson_id'], []).append(attempt)
    shown = {}
    for item in items:
        attempts = by_lesson.get(item['lesson_id'], [])
        # The open attempt wins (that is what "Continue" resumes); otherwise the most recent finished one.
        pick = next((a for a in attempts if a['status'] == 'in_progress'), attempts[0] if attempts else None)
        shown[item['lesson_id']] = pick
    ids = [a['id'] for a in shown.values() if a]
    decided, scored, spoken = _decided_counts(db, ids), _scored_totals(db, [a['version_id'] for a in shown.values() if a]), _spoken_states(db, ids)
    for item in items:
        pick = shown[item['lesson_id']]
        item.update({'status': 'new', 'attempt_id': None, 'answered': 0, 'total': 0, 'correct': None, 'scored': None, 'spoken_status': None,
                     'last_activity_at': None, 'completed_count': sum(1 for a in by_lesson.get(item['lesson_id'], []) if a['status'] == 'completed')})
        if pick:
            item.update({'status': 'in_progress' if pick['status'] == 'in_progress' else 'completed', 'attempt_id': pick['id'],
                         'answered': decided[pick['id']], 'total': pick['total'], 'spoken_status': spoken.get(pick['id']),
                         'last_activity_at': pick['completed_at'] or pick['created_at']})
            if pick['status'] == 'completed':
                item.update({'correct': pick['correct'], 'scored': scored.get(pick['version_id'])})


def progress_summary(db, user_id: str, limit: int = 8) -> dict:
    """Only facts: how many lessons the learner finished and the results of their finished attempts. No streaks, no levels, no mastery."""
    attempts = db.execute(select(t.attempts.c.id, t.attempts.c.status, t.attempts.c.version_id, t.attempts.c.correct, t.attempts.c.created_at,
                                 t.attempts.c.completed_at, t.attempts.c.audio_variant_id, t.versions.c.lesson_id, t.versions.c.title, t.versions.c.cefr)
                          .join(t.versions, t.versions.c.id == t.attempts.c.version_id).where(t.attempts.c.user_id == user_id)).mappings().all()
    finished = [a for a in attempts if a['status'] == 'completed']
    accent_of = dict(db.execute(select(t.audio.c.id, t.audio.c.accent).where(t.audio.c.id.in_({a['audio_variant_id'] for a in finished}))).all()) if finished else {}
    by_accent: dict[str, set] = {}
    by_level: dict[str, set] = {}
    for a in finished:                                   # distinct lessons, so replaying one lesson is not counted twice
        by_accent.setdefault(accent_of.get(a['audio_variant_id'], 'unknown'), set()).add(a['lesson_id'])
        by_level.setdefault(a['cefr'], set()).add(a['lesson_id'])
    scored = _scored_totals(db, list({a['version_id'] for a in finished}))
    recent = sorted(attempts, key=lambda a: a['completed_at'] or a['created_at'], reverse=True)[:limit]
    spoken = _spoken_states(db, [a['id'] for a in recent])
    available = db.execute(select(func.count(func.distinct(t.versions.c.lesson_id))).where(t.versions.c.status == 'published')).scalar_one()
    return {
        'lessons_available': available,
        'lessons_completed': len({a['lesson_id'] for a in finished}),
        'attempts_completed': len(finished),
        'questions_correct': sum(a['correct'] or 0 for a in finished),
        'questions_scored': sum(scored.get(a['version_id'], 0) for a in finished),
        'accent_counts': {k: len(v) for k, v in sorted(by_accent.items())},
        'level_counts': {k: len(v) for k, v in sorted(by_level.items())},
        'recent': [{'attempt_id': a['id'], 'lesson_id': a['lesson_id'], 'title': a['title'], 'cefr': a['cefr'], 'status': a['status'],
                    'when': a['completed_at'] or a['created_at'], 'correct': a['correct'], 'scored': scored.get(a['version_id']) if a['status'] == 'completed' else None,
                    'spoken_status': spoken.get(a['id'])} for a in recent],
    }


def _public_audio(db, version_id: str) -> list[dict]:
    rows = db.execute(select(t.audio.c.id, t.audio.c.accent, t.audio.c.voice, t.audio.c.duration_ms, t.audio.c.alignment)
                      .where(t.audio.c.version_id == version_id).order_by(t.audio.c.accent, t.audio.c.voice)).mappings().all()
    return [dict(r) for r in rows]


def lesson_detail(db, lesson_id: str, user_id: str | None = None) -> dict:
    version = db.execute(_latest_published(lesson_id)).mappings().first()
    if not version or not lesson_visible(db, user_id, lesson_id):
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


def _public_failure(code: str | None) -> str | None:
    """Learners may know their recording was lost or the check was unavailable, not which provider setting is wrong."""
    if code in (None, 'recording_missing', 'upload_incomplete', 'lease_expired', 'provider_unavailable', 'unusable_assessment'):
        return code
    if code in ('ai_budget', 'ai_halted'):
        return 'provider_unavailable'                  # paused or over budget: to the learner it is simply unavailable right now
    return 'provider_error'


def _spoken_view(row, rubric: dict) -> dict:
    """What the learner may see of their spoken answer. Key points appear only once an assessment exists for this answer."""
    state = row['status']
    out = {'status': state, 'retryable': False, 'transcript': row['transcript'], 'insufficient': False, 'points': None,
           'covered': None, 'partly': None, 'total_points': None, 'failure_code': _public_failure(row['failure_code']) if state == 'failed' else None}
    if state == 'failed':
        out['retryable'] = row['generation'] < MAX_SPOKEN_TRIES    # assess again if the transcript exists, otherwise record again
    if state == 'assessed':
        result = row['assessment'] or {}
        if result.get('insufficient'):
            out['insufficient'] = True
        else:
            text = {p['id']: p['text'] for p in rubric['points']}
            out['points'] = [{'id': v['id'], 'text': text.get(v['id'], ''), 'state': v['state'], 'quote': v['quote']} for v in result.get('points', [])]
            out['covered'] = sum(1 for v in out['points'] if v['state'] == 'covered')
            out['partly'] = sum(1 for v in out['points'] if v['state'] == 'partly')
            out['total_points'] = len(out['points'])
    return out


def attempt_view(db, user_id: str, attempt_id: str) -> dict:
    attempt = _owned_attempt(db, user_id, attempt_id)
    version = db.execute(select(t.versions).where(t.versions.c.id == attempt['version_id'])).mappings().one()
    questions = db.execute(select(t.exercises.c.id, t.exercises.c.position, t.exercises.c.kind, t.exercises.c.prompt, t.exercises.c.options)
                           .where(t.exercises.c.version_id == version['id']).order_by(t.exercises.c.position)).mappings().all()
    answered = {r['exercise_id']: r for r in db.execute(
        select(t.responses.c.exercise_id, t.responses.c.option_id, t.responses.c.is_correct, t.keys.c.correct_option_id, t.keys.c.explanation)
        .join(t.keys, t.keys.c.exercise_id == t.responses.c.exercise_id)
        .where(t.responses.c.attempt_id == attempt_id)).mappings().all()}
    given = {r['exercise_id']: r for r in db.execute(
        select(t.answers.c.exercise_id, t.answers.c.answer, t.answers.c.is_correct, t.rubrics.c.data)
        .join(t.rubrics, t.rubrics.c.exercise_id == t.answers.c.exercise_id).where(t.answers.c.attempt_id == attempt_id)).mappings().all()}
    spoken = {r['exercise_id']: r for r in db.execute(select(t.spoken).where(t.spoken.c.attempt_id == attempt_id)).mappings().all()}
    rubrics = {r.exercise_id: r.data for r in db.execute(select(t.rubrics.c.exercise_id, t.rubrics.c.data).where(
        t.rubrics.c.exercise_id.in_([q['id'] for q in questions if q['kind'] == 'spoken']))).all()} if spoken else {}
    audio = next((a for a in _public_audio(db, version['id']) if a['id'] == attempt['audio_variant_id']), None)

    def exercise(q) -> dict:
        out = {**q, 'feedback': None, 'fill': None, 'sequence': None, 'spoken': None}
        if q['id'] in answered:
            a = answered[q['id']]
            out['feedback'] = {'selected_option_id': a['option_id'], 'is_correct': a['is_correct'],
                               'correct_option_id': a['correct_option_id'], 'explanation': a['explanation']}
        elif q['id'] in given:
            a, rubric = given[q['id']], given[q['id']]['data']
            if q['kind'] == 'fill_blank':
                out['fill'] = {'answer': a['answer']['text'], 'is_correct': a['is_correct'], 'accepted': rubric['accepted'][0],
                               'explanation': rubric['explanation']}
            else:
                order = a['answer']['order']
                out['sequence'] = {'order': order, 'is_correct': a['is_correct'], 'correct_order': rubric['order'],
                                   'positions_correct': sum(1 for x, y in zip(order, rubric['order']) if x == y), 'explanation': rubric['explanation']}
        elif q['id'] in spoken and q['kind'] == 'spoken':
            out['spoken'] = _spoken_view(spoken[q['id']], rubrics.get(q['id'], {'points': []}))
        return out
    return {
        'attempt': {'id': attempt['id'], 'status': attempt['status'], 'total': attempt['total'], 'correct': attempt['correct'],
                    'scored': sum(1 for q in questions if q['kind'] != 'spoken'),
                    'created_at': attempt['created_at'], 'completed_at': attempt['completed_at']},
        'lesson': {'lesson_id': version['lesson_id'], 'version_id': version['id'], 'version_no': version['version_no'],
                   **{k: version[k] for k in ('title', 'topic', 'cefr', 'listen_for', 'speakers', 'sentences')}},
        'audio': audio,
        'exercises': [exercise(q) for q in questions],
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
    if not version or not lesson_visible(db, user_id, lesson_id):          # another learner's generated lesson answers exactly like an unknown one
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


MAX_SPOKEN_TRIES = 3


def _settle(db, attempt) -> None:
    """Complete the attempt when every exercise has a learner decision. A spoken answer that is still being assessed counts as decided:
    the learner must be able to move on, and the assessment is reported separately."""
    if attempt['status'] != 'in_progress':
        return
    mc = db.execute(select(func.count(), func.coalesce(func.sum(case((t.responses.c.is_correct, 1), else_=0)), 0))
                    .where(t.responses.c.attempt_id == attempt['id'])).first()
    other = db.execute(select(func.count(), func.coalesce(func.sum(case((t.answers.c.is_correct, 1), else_=0)), 0))
                       .where(t.answers.c.attempt_id == attempt['id'])).first()
    voiced = db.execute(select(func.count()).where(and_(t.spoken.c.attempt_id == attempt['id'],
                                                         t.spoken.c.status.in_(('skipped', 'pending', 'assessed', 'failed'))))).scalar_one()
    if mc[0] + other[0] + voiced >= attempt['total']:
        db.execute(update(t.attempts).where(t.attempts.c.id == attempt['id']).values(
            status='completed', correct=int(mc[1] + other[1]), completed_at=utcnow()))


def submit_answer(db, user_id: str, attempt_id: str, exercise_id: str, option_id: str | None = None, text: str | None = None,
                  order: list[str] | None = None) -> bool:
    """Record one answer exactly once. Returns True for a replay of the identical answer; a different second answer is refused."""
    attempt = _owned_attempt(db, user_id, attempt_id, lock=True)
    exercise = db.execute(select(t.exercises.c.id, t.exercises.c.kind, t.exercises.c.options).where(and_(
        t.exercises.c.id == exercise_id, t.exercises.c.version_id == attempt['version_id']))).mappings().first()
    if not exercise:
        raise NotFound('Question not found in this attempt.')
    kind = exercise['kind']
    if kind == 'spoken':
        raise Invalid('Record your answer, or skip this question.')
    if (kind == 'multiple_choice') != (option_id is not None) or (kind == 'fill_blank') != (text is not None) or (kind == 'sequencing') != (order is not None):
        raise Invalid('That answer does not match the question.')
    if kind == 'multiple_choice':
        return _answer_choice(db, attempt, exercise, option_id)
    return _answer_other(db, attempt, exercise, text, order)


def _answer_choice(db, attempt, exercise, option_id: str) -> bool:
    attempt_id, exercise_id = attempt['id'], exercise['id']
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
    _settle(db, attempt)
    return False


def _answer_other(db, attempt, exercise, text: str | None, order: list[str] | None) -> bool:
    attempt_id, exercise_id = attempt['id'], exercise['id']
    if text is not None:
        text = ' '.join(text.split())
        if not 1 <= len(text) <= 80:
            raise Invalid('Type a word or short phrase for the gap.')
        answer = {'text': text}
    else:
        if sorted(order) != sorted(o['id'] for o in exercise['options']):
            raise Invalid('Order every item exactly once.')
        answer = {'order': list(order)}
    existing = db.execute(select(t.answers.c.answer).where(and_(t.answers.c.attempt_id == attempt_id, t.answers.c.exercise_id == exercise_id))).scalar_one_or_none()
    if existing is not None:
        same = normalize_text(existing['text']) == normalize_text(text) if text is not None else existing.get('order') == order
        if same:
            return True
        raise Conflict('This question was already answered.')
    if attempt['status'] != 'in_progress':
        raise Conflict('This attempt is already complete.')
    rubric = db.execute(select(t.rubrics.c.data).where(t.rubrics.c.exercise_id == exercise_id)).scalar_one()
    correct = score_fill_blank(FillKey(**rubric), text) if text is not None else score_sequence(SequenceKey(**rubric), order)
    db.execute(insert(t.answers).values(attempt_id=attempt_id, exercise_id=exercise_id, version_id=attempt['version_id'], answer=answer,
                                        is_correct=correct, submitted_at=utcnow()))
    _settle(db, attempt)
    return False
