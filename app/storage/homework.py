"""Owned transactions; provider waits happen outside database transactions."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.storage import schema as s
from app.storage.repository import utcnow, aware
from app.homework import Assignment, ListeningContent, objective


def owner(repo):
    return s.homework.c.user_id == repo.learner_id


def row_lock(repo, db):
    db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == repo.learner_id).with_for_update()).scalar_one()


_UNLOADED = object()


def view(db, row, loaded_content=_UNLOADED):
    content = None
    if row['content_id']:
        data = (db.execute(select(s.listening_content.c.data).where(s.listening_content.c.id == row['content_id'])).scalar_one()
                if loaded_content is _UNLOADED else loaded_content)
        content = ListeningContent.model_validate(data)
    return Assignment(id=row['id'], lesson_id=row['lesson_id'], status=row['status'], objective=row['objective'], topic=row['topic'], content=content,
        discovery_in_progress=bool(row['lease_until'] and aware(row['lease_until']) > utcnow()),
        message='No suitable listening assignment is available right now. Your lesson is saved.' if row['status'] == 'unavailable' else None)


def list_assignments(repo):
    with repo.read() as db:
        rows = db.execute(select(s.homework).where(owner(repo)).order_by(s.homework.c.created_at.desc()).limit(12)).mappings().all()
        identifiers = {row['content_id'] for row in rows if row['content_id']}
        contents = dict(db.execute(select(s.listening_content.c.id, s.listening_content.c.data)
            .where(s.listening_content.c.id.in_(identifiers))).all()) if identifiers else {}
        return [view(db, row, contents.get(row['content_id'])) for row in rows]


def offer(repo, lesson_id):
    with repo.transaction() as db:
        row_lock(repo, db)
        lesson = db.execute(select(s.lessons).where(s.lessons.c.id == lesson_id, s.lessons.c.user_id == repo.learner_id)).mappings().first()
        if not lesson:
            raise LookupError()
        if lesson['status'] != 'completed':
            raise ValueError('Finish your lesson before choosing optional homework.')
        row = db.execute(select(s.homework).where(owner(repo), s.homework.c.lesson_id == lesson_id)).mappings().first()
        if row:
            return view(db, row)
        at = utcnow()
        inserted = db.execute(s.homework.insert().values(id=str(uuid4()), user_id=repo.learner_id, lesson_id=lesson_id,
            status='offered', objective=objective(lesson['data']['focus']), attempts=0, created_at=at, updated_at=at)
            .returning(*s.homework.c)).mappings().one()
        return view(db, inserted)


def reserve(repo, assignment_id, accept, topic, daily_limit):
    with repo.transaction() as db:
        row_lock(repo, db)
        row = db.execute(select(s.homework).where(owner(repo), s.homework.c.id == assignment_id).with_for_update()).mappings().first()
        if not row:
            raise LookupError()
        if row['status'] not in ('offered', 'unavailable'):
            return view(db, row), None
        at = utcnow()
        if row['lease_until'] and aware(row['lease_until']) > at:
            raise ValueError('Your optional listening assignment is being prepared. Please wait a moment.')
        if not accept:
            db.execute(update(s.homework).where(owner(repo), s.homework.c.id == assignment_id).values(status='skipped', updated_at=at))
            return Assignment(id=row['id'], lesson_id=row['lesson_id'], status='skipped', objective=row['objective']), None
        if row['attempts'] >= 2:
            raise ValueError('No more searches for this lesson. Continue with your next lesson when ready.')
        admit(db, repo, daily_limit, at)
        token = str(uuid4())
        db.execute(update(s.homework).where(owner(repo), s.homework.c.id == assignment_id).values(
            attempts=row['attempts'] + 1, topic=topic, lease_token=token, lease_until=at + timedelta(seconds=30), updated_at=at))
        return view(db, row), token


def finish(repo, assignment_id, token, candidates):
    with repo.transaction() as db:
        row_lock(repo, db)
        row = db.execute(select(s.homework).where(owner(repo), s.homework.c.id == assignment_id).with_for_update()).mappings().first()
        if not row:
            raise LookupError()
        if row['lease_token'] != token:
            return view(db, row)
        selected_id = None
        selected_content = None
        keys = {(content.provider, content.provider_id) for content in candidates}
        existing = {(item.provider, item.provider_id): item.id for item in db.execute(
            select(s.listening_content.c.provider, s.listening_content.c.provider_id, s.listening_content.c.id)
            .where(tuple_(s.listening_content.c.provider, s.listening_content.c.provider_id).in_(keys)))} if keys else {}
        used = set(db.execute(select(s.homework.c.content_id).where(owner(repo),
            s.homework.c.content_id.in_(existing.values()))).scalars()) if existing else set()
        for content in candidates:
            stored_id = existing.get((content.provider, content.provider_id))
            if stored_id in used:
                continue
            if stored_id is None:
                insert = pg_insert if repo.engine.dialect.name == 'postgresql' else sqlite_insert
                # Resolve a cross-account discovery race without replacing the shared PK.
                stored_id = db.execute(insert(s.listening_content).values(id=content.id, provider=content.provider,
                    provider_id=content.provider_id, data=content.model_dump(mode='json'),
                    validated_at=content.validated_at).on_conflict_do_nothing()
                    .returning(s.listening_content.c.id)).scalar_one_or_none()
                if stored_id is None:
                    stored_id = db.execute(select(s.listening_content.c.id).where(s.listening_content.c.provider == content.provider,
                        s.listening_content.c.provider_id == content.provider_id)).scalar_one()
            selected_id = stored_id
            content.id = selected_id
            selected_content = content.model_dump(mode='json')
            db.execute(update(s.listening_content).where(s.listening_content.c.id == selected_id).values(
                data=selected_content, validated_at=content.validated_at))
            break
        changed = db.execute(update(s.homework).where(owner(repo), s.homework.c.id == assignment_id).values(content_id=selected_id,
            status='assigned' if selected_id else 'unavailable', lease_token=None, lease_until=None, updated_at=utcnow())
            .returning(*s.homework.c)).mappings().one()
        return view(db, changed, selected_content)


def get(repo, assignment_id):
    with repo.read() as db:
        row = db.execute(select(*s.homework.c, s.listening_content.c.data.label('_content'))
            .select_from(s.homework.outerjoin(s.listening_content, s.listening_content.c.id == s.homework.c.content_id))
            .where(owner(repo), s.homework.c.id == assignment_id)).mappings().first()
        if not row:
            raise LookupError()
        return view(db, row, row['_content'])


def decision_context(repo, assignment_id):
    """Fresh owned assignment/lesson/profile, never retained across provider work."""
    from app.lesson import Lesson
    from app.models import LearnerProfile
    with repo.read() as db:
        row = db.execute(select(*s.homework.c, s.listening_content.c.data.label('_content'),
            s.lessons.c.data.label('_lesson'), s.profiles.c.data.label('_profile')).select_from(
                s.homework.join(s.profiles, s.profiles.c.user_id == s.homework.c.user_id)
                .outerjoin(s.lessons, (s.lessons.c.id == s.homework.c.lesson_id) & (s.lessons.c.user_id == repo.learner_id))
                .outerjoin(s.listening_content, s.listening_content.c.id == s.homework.c.content_id))
            .where(owner(repo), s.homework.c.id == assignment_id)).mappings().first()
        if row is None:
            raise LookupError()
        return (view(db, row, row['_content']), Lesson.model_validate(row['_lesson']) if row['_lesson'] else None,
                LearnerProfile.model_validate(row['_profile']))


def change_status(repo, assignment_id, status):
    with repo.transaction() as db:
        row_lock(repo, db)
        row = db.execute(select(s.homework).where(owner(repo), s.homework.c.id == assignment_id).with_for_update()).mappings().first()
        if not row:
            raise LookupError()
        if row['status'] != status and row['status'] != 'assigned':
            raise ValueError('This assignment is no longer active.')
        db.execute(update(s.homework).where(owner(repo), s.homework.c.id == assignment_id).values(status=status, updated_at=utcnow()))
        return view(db, {**row, 'status':status})


def revalidated(repo, assignment_id, content):
    with repo.transaction() as db:
        row_lock(repo, db)
        row = db.execute(select(s.homework).where(owner(repo), s.homework.c.id == assignment_id).with_for_update()).mappings().first()
        if not row:
            raise LookupError()
        if row['status'] != 'assigned':
            return view(db, row)
        if content is None:
            db.execute(update(s.homework).where(owner(repo), s.homework.c.id == assignment_id).values(status='unavailable', updated_at=utcnow()))
        else:
            content.id = row['content_id']
            db.execute(update(s.listening_content).where(s.listening_content.c.id == content.id).values(
                data=content.model_dump(mode='json'), validated_at=content.validated_at))
        return view(db, db.execute(select(s.homework).where(owner(repo), s.homework.c.id == assignment_id)).mappings().one())


def admit(db, repo, daily_limit, at):
    # One locked fleet counter survives restarts and bounds all workers.
    insert = pg_insert if repo.engine.dialect.name == 'postgresql' else sqlite_insert
    db.execute(insert(s.homework_budget).values(id=1, day=at.date().isoformat(), searches=0).on_conflict_do_nothing())
    budget = db.execute(select(s.homework_budget).where(s.homework_budget.c.id == 1).with_for_update()).mappings().one()
    used = budget['searches'] if budget['day'] == at.date().isoformat() else 0
    if used >= daily_limit:
        raise ValueError('Optional listening discovery is resting today. Your lesson is saved.')
    db.execute(update(s.homework_budget).where(s.homework_budget.c.id == 1).values(day=at.date().isoformat(), searches=used + 1))


def admit_validation(repo, daily_limit):
    with repo.transaction() as db:
        admit(db, repo, daily_limit, utcnow())
