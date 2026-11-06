"""Curated shared library: the operator-only workflow that turns a private generated lesson into a published shared one, and back.

Visibility is NOT decided here. It stays exactly what `store._visible` already says: a lesson with no owner row is shared. This module only moves a lesson
across that line, in one transaction, after a recorded review of the exact version:

    generate (private, owned by the curator)  ->  register (draft)  ->  approve (reviewed version)  ->  publish (owner row removed)  ->  unpublish (owner restored)

* Drafts are private by construction: the lesson is owned, so no other learner can see it, play it, attempt it or find it in the catalog.
* Publishing never copies audio: the shared lesson keeps its content-addressed object key.
* Nothing here is reachable from a learner route. It runs as `python -m app.listening.library ...` with the database owner's credentials, because the
  API's runtime role deliberately cannot delete owner rows. A compromised or misused API therefore cannot publish anything.
* Unreviewed content cannot be published: publish needs state `approved`, the same version that was approved, and a fresh readiness check.
* Account deletion: a draft or unpublished lesson belongs to its curator and is erased with them; a published lesson has no owner and outlives them.
"""
import argparse
import json
import sys

from sqlalchemy import select, insert, update, delete, and_

from app.listening import schema as t
from app.listening.store import Conflict, Invalid, NotFound, _latest_published
from app.storage.repository import utcnow

CATEGORIES = ('everyday', 'workplace', 'travel', 'culture', 'stories')
REQUIRED_KINDS = ('multiple_choice', 'spoken', 'fill_blank', 'sequencing')
STATES = ('draft', 'approved', 'published', 'unpublished')


def _owner(db, lesson_id: str):
    return db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == lesson_id)).scalar_one_or_none()


def _latest(db, lesson_id: str):
    row = db.execute(_latest_published(lesson_id)).mappings().first()
    if not row:
        raise NotFound('That lesson has no published version.')
    return row


def _entry(db, lesson_id: str, lock: bool = False):
    query = select(t.library_entries).where(t.library_entries.c.lesson_id == lesson_id)
    row = db.execute(query.with_for_update() if lock else query).mappings().first()
    if not row:
        raise NotFound('That lesson is not registered in the library. Run register first.')
    return row


def readiness(db, version_id: str) -> list[str]:
    """What is missing before a version may be approved or published. Empty = complete: audio, a transcript, and all four exercises with keys."""
    problems = []
    version = db.execute(select(t.versions).where(t.versions.c.id == version_id)).mappings().first()
    if not version or version['status'] != 'published':
        return ['the version is not a published version']
    if not version['sentences']:
        problems.append('no transcript')
    if not version['speakers']:
        problems.append('no speakers')
    audio = db.execute(select(t.audio.c.duration_ms, t.audio.c.alignment).where(t.audio.c.version_id == version_id)).all()
    if not audio:
        problems.append('no audio variant')
    elif any(len(a.alignment or []) != len(version['sentences']) for a in audio):
        problems.append('the audio is not aligned to every sentence')
    rows = db.execute(select(t.exercises.c.id, t.exercises.c.kind).where(t.exercises.c.version_id == version_id)).all()
    kinds = sorted(k for _, k in rows)
    if kinds != sorted(REQUIRED_KINDS):
        problems.append(f'exercises are {kinds}, expected one of each of {list(REQUIRED_KINDS)}')
    ids = [i for i, _ in rows]
    # Multiple choice keeps its key in listening_exercise_keys; the spoken key points, accepted blank answers and correct order are private rubrics.
    keyed = {r[0] for r in db.execute(select(t.keys.c.exercise_id).where(t.keys.c.exercise_id.in_(ids)))} if ids else set()
    rubric = {r[0] for r in db.execute(select(t.rubrics.c.exercise_id).where(t.rubrics.c.exercise_id.in_(ids)))} if ids else set()
    if any(i not in (keyed if k == 'multiple_choice' else rubric) for i, k in rows):
        problems.append('an exercise has no answer key or rubric')
    return problems


def register(db, lesson_id: str, category: str) -> dict:
    """Start the review of a private lesson. The lesson must currently be owned (a draft); an already-shared lesson is never adopted."""
    if category not in CATEGORIES:
        raise Invalid(f'category must be one of {list(CATEGORIES)}')
    version = _latest(db, lesson_id)
    curator = _owner(db, lesson_id)
    if curator is None:
        raise Conflict('That lesson is already shared (or has no owner); only a private generated lesson can be registered.')
    if db.execute(select(t.library_entries.c.lesson_id).where(t.library_entries.c.lesson_id == lesson_id)).first():
        raise Conflict('That lesson is already registered.')
    now = utcnow()
    db.execute(insert(t.library_entries).values(lesson_id=lesson_id, version_id=version['id'], state='draft', category=category, curator_id=curator,
                                                created_at=now, updated_at=now))
    return {'lesson_id': lesson_id, 'state': 'draft', 'version_id': version['id']}


def approve(db, lesson_id: str, reviewer: str, note: str = '') -> dict:
    """Record that a named reviewer has listened to, read and tried the exact version. Only from draft or unpublished; a rejected-readiness lesson is refused."""
    reviewer, note = (reviewer or '').strip(), (note or '').strip()
    if not reviewer or len(reviewer) > 80 or len(note) > 500:
        raise Invalid('A reviewer name (up to 80 characters) is required; a note is optional (up to 500).')
    entry = _entry(db, lesson_id, lock=True)
    if entry['state'] not in ('draft', 'unpublished'):
        raise Conflict(f'A lesson in state {entry["state"]} cannot be approved.')
    if _latest(db, lesson_id)['id'] != entry['version_id']:
        raise Conflict('The lesson has a newer version than the one registered; register a new lesson instead of approving stale content.')
    problems = readiness(db, entry['version_id'])
    if problems:
        raise Invalid('Not ready: ' + '; '.join(problems))
    now = utcnow()
    db.execute(update(t.library_entries).where(t.library_entries.c.lesson_id == lesson_id)
               .values(state='approved', reviewer=reviewer, review_note=note or None, approved_at=now, updated_at=now))
    return {'lesson_id': lesson_id, 'state': 'approved', 'reviewer': reviewer}


def publish(db, lesson_id: str) -> dict:
    """Make an approved lesson shared: remove its owner row. One transaction; the entry row is locked so two operators cannot interleave."""
    entry = _entry(db, lesson_id, lock=True)
    if entry['state'] != 'approved':
        raise Conflict(f'Only an approved lesson can be published (this one is {entry["state"]}).')
    if _latest(db, lesson_id)['id'] != entry['version_id']:
        raise Conflict('The lesson changed after it was approved; approval does not carry over.')
    problems = readiness(db, entry['version_id'])
    if problems:
        raise Invalid('Not ready: ' + '; '.join(problems))
    owner = _owner(db, lesson_id)
    if owner is None:
        raise Conflict('The lesson has no owner row, so it is already visible to everyone; refusing to record it as published.')
    db.execute(delete(t.lesson_owners).where(t.lesson_owners.c.lesson_id == lesson_id))
    now = utcnow()
    db.execute(update(t.library_entries).where(t.library_entries.c.lesson_id == lesson_id)
               .values(state='published', curator_id=owner, published_at=now, updated_at=now))
    return {'lesson_id': lesson_id, 'state': 'published'}


def unpublish(db, lesson_id: str, to_user: str | None = None) -> dict:
    """Withdraw a shared lesson: it becomes private to the curator again (or to `to_user` if the curator's account is gone). Learners lose access at once;
    their past attempts are kept but the lesson answers like an unknown one."""
    entry = _entry(db, lesson_id, lock=True)
    if entry['state'] != 'published':
        raise Conflict(f'Only a published lesson can be unpublished (this one is {entry["state"]}).')
    target = to_user or entry['curator_id']
    if not target:
        raise Invalid('The curator account no longer exists; pass --to-user with the account that should hold it.')
    if _owner(db, lesson_id) is not None:
        raise Conflict('The lesson already has an owner; nothing to restore.')
    now = utcnow()
    db.execute(insert(t.lesson_owners).values(lesson_id=lesson_id, user_id=target, created_at=now))
    db.execute(update(t.library_entries).where(t.library_entries.c.lesson_id == lesson_id)
               .values(state='unpublished', curator_id=target, unpublished_at=now, updated_at=now))
    return {'lesson_id': lesson_id, 'state': 'unpublished'}


def status(db) -> list[dict]:
    rows = db.execute(select(t.library_entries.c.lesson_id, t.library_entries.c.state, t.library_entries.c.category, t.library_entries.c.reviewer,
                             t.library_entries.c.approved_at, t.library_entries.c.published_at, t.lessons.c.slug, t.versions.c.title, t.versions.c.cefr)
                      .join(t.lessons, t.lessons.c.id == t.library_entries.c.lesson_id)
                      .join(t.versions, t.versions.c.id == t.library_entries.c.version_id)
                      .order_by(t.versions.c.cefr, t.versions.c.title)).mappings().all()
    return [{k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in dict(r).items()} for r in rows]


def review_sheet(db, lesson_id: str) -> dict:
    """Everything a reviewer needs, INCLUDING answer keys: this is the operator's review output, never a learner response."""
    entry = _entry(db, lesson_id)
    version = db.execute(select(t.versions).where(t.versions.c.id == entry['version_id'])).mappings().one()
    exercises = db.execute(select(t.exercises.c.id, t.exercises.c.position, t.exercises.c.kind, t.exercises.c.prompt, t.exercises.c.options)
                           .where(t.exercises.c.version_id == entry['version_id']).order_by(t.exercises.c.position)).mappings().all()
    ids = [e['id'] for e in exercises]
    keys = {k.exercise_id: {'correct_option_id': k.correct_option_id, 'explanation': k.explanation} for k in db.execute(select(t.keys).where(t.keys.c.exercise_id.in_(ids)))}
    keys.update({r.exercise_id: r.data for r in db.execute(select(t.rubrics).where(t.rubrics.c.exercise_id.in_(ids)))})
    return {'lesson_id': lesson_id, 'state': entry['state'], 'category': entry['category'], 'title': version['title'], 'cefr': version['cefr'],
            'speakers': version['speakers'], 'transcript': [s.get('text') if isinstance(s, dict) else s for s in version['sentences']],
            'exercises': [{**dict(e), 'answer': keys.get(e['id'])} for e in exercises], 'problems': readiness(db, entry['version_id'])}


def main(argv: list[str]) -> int:
    """python -m app.listening.library status | review ID | register ID --category C | approve ID --reviewer NAME [--note T] | publish ID | unpublish ID [--to-user U]"""
    parser = argparse.ArgumentParser(prog='python -m app.listening.library', description='Operator tool for the curated shared library (database-owner credentials).')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    for name in ('review', 'register', 'approve', 'publish', 'unpublish'):
        p = sub.add_parser(name)
        p.add_argument('lesson_id')
        if name == 'register':
            p.add_argument('--category', required=True, choices=CATEGORIES)
        if name == 'approve':
            p.add_argument('--reviewer', required=True)
            p.add_argument('--note', default='')
        if name == 'unpublish':
            p.add_argument('--to-user')
    args = parser.parse_args(argv)
    from app.config import Settings
    from app.listening.worker import make_engine
    engine = make_engine(Settings())
    try:
        with engine.begin() as db:
            if args.action == 'status':
                out = status(db)
            elif args.action == 'review':
                out = review_sheet(db, args.lesson_id)
            elif args.action == 'register':
                out = register(db, args.lesson_id, args.category)
            elif args.action == 'approve':
                out = approve(db, args.lesson_id, args.reviewer, args.note)
            elif args.action == 'publish':
                out = publish(db, args.lesson_id)
            else:
                out = unpublish(db, args.lesson_id, args.to_user)
    except (NotFound, Conflict, Invalid) as problem:
        print(f'Refused: {problem}', file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
