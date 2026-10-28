"""Atomic invalidation for closing/expiry/reset, including lessons without exercises."""
from sqlalchemy import select,update,func
from app.storage import schema as s
from app.lesson import compose_closing
from app.lesson_limit import window_phase
from experiments.durable_claims.store import slots,Conflict
from .schema import operations
from .store import StageStore


def invalidate(db,owner):
    slot=db.execute(select(slots).where(slots.c.owner==owner).with_for_update()).mappings().first()
    if not slot:return
    if slot['active']:
        known=db.execute(select(operations.c.id).where(operations.c.owner==owner,operations.c.id==slot['active'])).first()
        if not known:raise Conflict('Unknown ownership protocol')
    db.execute(update(operations).where(operations.c.owner==owner,operations.c.status=='running').values(status='invalidated'))
    db.execute(update(slots).where(slots.c.owner==owner).values(active=None,fence=slots.c.fence+1))


def reset(repo,settings,revision):
    from app.onboarding_reset import reset_onboarding
    from app.limits import LessonLimits
    st=StageStore(repo,settings)
    with repo.transaction() as db:
        from .bridge import advisory
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        bound=st.repo_bound(db)
        LessonLimits(bound,settings).block_onboarding_restart()
        invalidate(db,repo.learner_id)
        # Original CAS and deletion/checkpoint semantics share this transaction.
        return reset_onboarding(bound,revision)


def settle(repo,settings,lesson_id=None):
    if settings.lesson_limit_mode!='enforce':return
    st=StageStore(repo,settings)
    with repo.transaction() as db:
        from .bridge import advisory
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        bound=st.repo_bound(db);lesson=bound.lesson(lesson_id)
        if not lesson or lesson.status not in ('active','paused'):return
        at=db.execute(select(func.clock_timestamp())).scalar_one();phase=window_phase(lesson,at)
        if phase=='open':return
        if phase=='closing' and (lesson.phase in ('CLOSING','COMPLETED') or lesson.exercise_id and bound.completed(lesson.exercise_id)):return
        invalidate(db,repo.learner_id)
        if phase=='over':bound.expire_lesson(lesson.id,at)
        else:
            work=bound.lesson_work(lesson.id)
            compose_closing(lesson,[w['evaluation'] for w in work],[w['content']['topic'] for w in work],[w['content'] for w in work])
            lesson.phase='CLOSING';lesson.exercise_id=None
            bound.save_lesson(lesson,lesson.revision)


def record_again(repo,settings,identifier,kind):
    """Cancel only this recording's evidence slot; never an assessment or unrelated work."""
    from app.models import Conversation
    st=StageStore(repo,settings)
    with repo.transaction() as db:
        from .bridge import advisory
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        slot=db.execute(select(slots).where(slots.c.owner==repo.learner_id).with_for_update()).mappings().first()
        op=db.execute(select(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier,operations.c.kind==kind)).mappings().first()
        if not op or op['status'] not in ('running','complete') or not slot or slot['active'] not in (None,identifier):raise Conflict('Recording ownership changed')
        bound=st.repo_bound(db)
        if kind=='upload':
            row=bound.conversation(op['exercise'])
            if not row or any(t.attempt_id==identifier for t in row.turns) or row.pending_attempt_id not in (None,identifier):raise Conflict('Recording already assessed or replaced')
            db.execute(update(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier).values(status='invalidated'))
            db.execute(update(slots).where(slots.c.owner==repo.learner_id).values(active=None,fence=slots.c.fence+1))
            row.pending_attempt_id=row.pending_text=None;row.state='AWAITING_FOLLOWUP' if row.active_followup else 'AWAITING_SUMMARY'
            bound.save_conversation(row)
        elif kind=='request' and op['input'].get('path','').endswith('/attempts') and '/lessons/' in op['input']['path']:
            # The original revision and canonical checkpoint identify this check-in.
            lesson_id=op['input']['path'].split('/')[3]
            lesson=bound.lesson(lesson_id)
            receipt=op['snapshot'].get('receipt',{}).get('body',{})
            if not lesson or lesson.status!='active' or lesson.phase not in ('WELCOME','REVIEW') or (op['status']=='complete' and lesson.revision!=receipt.get('revision')):raise Conflict('Check-in changed')
            invalidate(db,repo.learner_id)
            lesson.pending=None;bound.save_lesson(lesson,lesson.revision)
        else:raise Conflict('Only a recording can be explicitly replaced')
        return {'record_again':True}


def replace_by_recording(repo,settings,resource,resource_id,recording,*,revision=None,pending=None,followup=None):
    """Explicit replacement also tombstones a send whose acknowledgment never arrived.

    A status-404 alone cannot prove the old body will not arrive later. The stable
    recording identity must be revoked before discarding the in-memory recording.
    """
    import re
    from uuid import uuid5
    from app.upload_idempotency import attempt_id_for,NAMESPACE
    if not re.fullmatch('[0-9a-f]{16}',recording):raise Conflict('Invalid recording identity')
    path=f'/api/lessons/{resource_id}/attempts'
    identifier=attempt_id_for(repo.learner_id,resource_id,recording) if resource=='exercise' else str(uuid5(NAMESPACE,repo.learner_id+'|request|POST|'+path+'|'+recording))
    kind='upload' if resource=='exercise' else 'request'
    st=StageStore(repo,settings)
    with repo.transaction() as db:
        from .bridge import advisory
        from sqlalchemy.dialects.postgresql import insert
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        db.execute(insert(slots).values(owner=repo.learner_id,fence=0).on_conflict_do_nothing())
        slot=db.execute(select(slots).where(slots.c.owner==repo.learner_id).with_for_update()).mappings().one()
        op=db.execute(select(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier)).mappings().first()
        if slot['active'] not in (None,identifier):raise Conflict('Recording ownership changed')
        bound=st.repo_bound(db)
        if resource=='exercise':
            ex=bound.exercise(resource_id);c=bound.conversation(resource_id)
            current=bound.current_exercise()
            if not ex or not current or current['id']!=resource_id or not c or bound.completed(resource_id):raise Conflict('Exercise changed')
            if c.pending_attempt_id!=pending or (c.active_followup.id if c.active_followup else None)!=followup or pending not in (None,identifier) or any(t.attempt_id==identifier for t in c.turns):raise Conflict('Question or recording evidence changed')
        elif resource=='lesson':
            lesson=bound.lesson(resource_id)
            if not lesson or lesson.status!='active' or lesson.phase not in ('WELCOME','REVIEW') or lesson.revision!=revision or (not op and lesson.pending is not None):raise Conflict('Check-in changed')
        else:raise Conflict('Unknown recording resource')
        marker={'resource':resource,'id':resource_id,'followup':followup if resource=='exercise' else None,'revision':revision}
        if op and op['status']=='invalidated':
            empty=c.pending_attempt_id is None if resource=='exercise' else lesson.pending is None
            if empty and op['input'].get('replacement')==marker:return {'record_again':True}
            raise Conflict('Replacement checkpoint changed')
        at=db.execute(select(func.clock_timestamp())).scalar_one();next_fence=slot['fence']+1
        if op:db.execute(update(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier).values(status='invalidated'))
        else:db.execute(operations.insert().values(owner=repo.learner_id,id=identifier,exercise=resource_id if resource=='exercise' else repo.learner_id,kind=kind,fingerprint='0'*64,
                fence=next_fence,status='invalidated',expires=at,snapshot={},input={} if resource=='exercise' else {'path':path,'method':'POST'}))
        db.execute(update(slots).where(slots.c.owner==repo.learner_id).values(active=None,fence=next_fence))
        if resource=='exercise':
            c.pending_attempt_id=c.pending_text=None;c.state='AWAITING_FOLLOWUP' if c.active_followup else 'AWAITING_SUMMARY';bound.save_conversation(c)
        elif lesson.pending is not None:
            lesson.pending=None;saved=bound.save_lesson(lesson,lesson.revision);marker['revision']=saved.revision
        db.execute(update(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier).values(input={**(op['input'] if op else {}),'replacement':marker}))
        return {'record_again':True}
