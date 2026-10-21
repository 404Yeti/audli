"""Bounded, ungraded homework self-report using schema005 reflection storage."""
import asyncio
import re
from uuid import uuid4
from typing import Literal
from fastapi import HTTPException, UploadFile, Form
from pydantic import Field
from sqlalchemy import select, update
from app.audio import validate_upload
from app.models import StrictModel, Transcription
from app.recognition import reliable_recognition
from app.homework_voice import SHORT_ASSESSMENT_WARNING
from app.storage import schema as s, homework as store
from app.storage.repository import utcnow

PROMPTS = {
    'watched': 'Did you get a chance to watch your optional listening? You can say yes, not yet, or skip.',
    'summary': 'In your own words, what main idea stayed with you?',
    'detail': 'What detail or example do you remember?',
    'done': 'Thanks for sharing. Let’s continue with today’s listening.',
}

class Review(StrictModel):
    revision: int = Field(ge=0)
    phase: Literal['watched', 'summary', 'detail', 'clarify', 'done']
    prompt: str
    grounding: Literal['unverified_self_report'] = 'unverified_self_report'

class Reply(StrictModel):
    revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)

class Revision(StrictModel):
    revision: int = Field(ge=0)


def choice(text):
    text = ' '.join(re.sub(r'[^a-z ]', ' ', text.lower()).split())
    if text in {'yes', 'yes please', 'yeah', 'yeah sure', 'i did', 'i watched it', 'yes i watched it', 'yes i did'}:
        return 'yes'
    if text in {'no', 'no thanks', 'not yet', 'no not yet', 'maybe next time', 'skip', 'skip it', 'i did not', 'not today'}:
        return 'skip'
    return None


def public(data):
    phase = data['phase']
    prompt = PROMPTS[phase] if phase != 'clarify' else (
        'I didn’t catch that. Did you watch it? Say yes, not yet, or skip.' if data['resume_phase'] == 'watched'
        else 'I didn’t catch that. Tell me one thing you remember, or say skip.')
    return Review(revision=data['revision'], phase=phase, prompt=prompt)


def prepare(repo, lesson_id):
    with repo.transaction() as db:
        store.row_lock(repo, db)
        lesson = repo.lesson(lesson_id)
        if lesson is None:
            raise LookupError()
        if lesson.status != 'active' or lesson.phase != 'TRANSITION':
            raise ValueError('Continue your current lesson checkpoint.')
        existing = db.execute(select(s.reflections).where(s.reflections.c.user_id == repo.learner_id,
            s.reflections.c.data['lesson_id'].as_string() == lesson_id).limit(1)).mappings().first()
        if existing:
            return public(existing['data'])
        assignment = db.execute(select(s.homework).where(store.owner(repo),
            s.homework.c.lesson_id != lesson_id, s.homework.c.status.in_(['assigned', 'completed']))
            .order_by(s.homework.c.created_at).limit(1)).mappings().first()
        if not assignment:
            return None
        # A paused older review must not ask this same assignment in a new lesson.
        prior = db.execute(select(s.reflections.c.id).where(s.reflections.c.user_id == repo.learner_id,
            s.reflections.c.assignment_id == assignment['id'],
            s.reflections.c.data['phase'].as_string() != 'done').limit(1)).first()
        if prior:
            return None
        data = {'lesson_id': lesson_id, 'phase': 'watched', 'revision': 0,
            'clarified': False, 'answers': {}}
        db.execute(s.reflections.insert().values(id=str(uuid4()), user_id=repo.learner_id,
            assignment_id=assignment['id'], created_at=utcnow(), data=data, grounding='unverified_self_report'))
        return public(data)


def saved(db, repo, lesson_id):
    row = db.execute(select(s.reflections).where(s.reflections.c.user_id == repo.learner_id,
        s.reflections.c.data['lesson_id'].as_string() == lesson_id).limit(1)).mappings().first()
    if not row:
        raise LookupError()
    return row


def answer(repo, lesson_id, revision, text, reliable=True, skip=False):
    with repo.transaction() as db:
        store.row_lock(repo, db)
        row = saved(db, repo, lesson_id)
        data = {**row['data'], 'answers': dict(row['data']['answers'])}
        if data['phase'] == 'done' or data['revision'] != revision:
            return public(data)
        lesson = repo.lesson(lesson_id)
        if not lesson or lesson.status != 'active' or lesson.phase != 'TRANSITION':
            raise ValueError('Continue your current lesson checkpoint.')
        phase = data.get('resume_phase') if data['phase'] == 'clarify' else data['phase']
        decision = choice(text) if reliable else None
        finish = 'skipped' if skip or decision == 'skip' else None
        if not finish:
            if not reliable or (phase == 'watched' and decision is None):
                if data['clarified']:
                    finish = 'skipped'
                else:
                    data.update(phase='clarify', resume_phase=phase, clarified=True)
            elif phase == 'watched':
                data['phase'] = 'summary'
            else:
                data['answers'][phase] = text.strip()
                if phase == 'summary':
                    data['phase'] = 'detail'
                else:
                    finish = 'discussed'
        if finish:
            data['phase'] = 'done'
            db.execute(update(s.homework).where(store.owner(repo), s.homework.c.id == row['assignment_id'],
                s.homework.c.status.in_(['assigned', 'completed'])).values(status=finish, updated_at=utcnow()))
        data['revision'] += 1
        db.execute(update(s.reflections).where(s.reflections.c.id == row['id'],
            s.reflections.c.user_id == repo.learner_id).values(data=data))
        return public(data)


def register(app, repo, ai, lock, settings):
    def enabled():
        if not settings.homework_enabled:
            raise HTTPException(404, 'Optional listening homework is disabled.')

    def owned(action, *args):
        try:
            return action(repo, *args)
        except LookupError:
            raise HTTPException(404, 'Homework review not found.') from None
        except ValueError:
            raise HTTPException(409, 'Continue your current lesson checkpoint.') from None

    @app.post('/api/lessons/{lesson_id}/homework-review', response_model=Review | None)
    async def start(lesson_id: str):
        enabled()
        return owned(prepare, lesson_id)

    @app.post('/api/lessons/{lesson_id}/homework-review/text', response_model=Review)
    async def typed(lesson_id: str, body: Reply):
        enabled()
        return owned(answer, lesson_id, body.revision, body.text)

    @app.post('/api/lessons/{lesson_id}/homework-review/skip', response_model=Review)
    async def skip(lesson_id: str, body: Revision):
        enabled()
        return owned(answer, lesson_id, body.revision, '', True, True)

    @app.post('/api/lessons/{lesson_id}/homework-review/audio')
    async def audio(lesson_id: str, body: Revision):
        enabled()
        review = owned(prepare, lesson_id)
        if not review or review.revision != body.revision:
            raise HTTPException(409, 'Continue your current homework review.')
        return await app.state.homework_speech(review.prompt, 'homework-review-' + review.phase)

    @app.post('/api/lessons/{lesson_id}/homework-review/attempts', response_model=Review)
    async def spoken(lesson_id: str, audio: UploadFile, revision: int = Form(ge=0)):
        try:
            enabled()
            async with lock:
                review = owned(prepare, lesson_id)
                if not review:
                    raise HTTPException(404, 'Homework review not found.')
                if review.phase == 'done' or review.revision != revision:
                    return review
                app.state.limits.admit('practice', 'homework.review.attempt', repo.lesson(lesson_id))
                data, filename = await validate_upload(audio, settings)
                try:
                    transcription = Transcription.model_validate(await asyncio.wait_for(ai().transcribe(data, filename), 15))
                except Exception:
                    raise HTTPException(503, 'Continue today’s lesson; your progress is saved.') from None
                reliable = reliable_recognition(transcription, settings.min_transcription_confidence)
                if choice(transcription.text):
                    reliable = bool(transcription.confidence is not None
                        and transcription.confidence >= settings.min_transcription_confidence
                        and not [w for w in transcription.uncertainty if w != SHORT_ASSESSMENT_WARNING])
                text = transcription.text if len(transcription.text) <= 2000 else ''
                return owned(answer, lesson_id, revision, text, reliable and bool(text))
        finally:
            await audio.close()
