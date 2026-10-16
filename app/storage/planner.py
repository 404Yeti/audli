"""Roadmap reconciliation under the same learner row lock as lesson completion."""
from sqlalchemy import select, and_
from app.storage import schema as s
from app.models import LearnerProfile
from app.lesson import Lesson
from app.storage.repository import aware
from app.planner import Roadmap, PlannerFeedback, PlanningContext, outlines, qualifying_count

def reconcile(repo, db, profile):
    row = db.execute(select(s.roadmaps.c.data).where(s.roadmaps.c.user_id == repo.learner_id)).scalar_one_or_none()
    roadmap = Roadmap.model_validate(row) if row else Roadmap()
    completed = [Lesson.model_validate(data) for data in db.execute(select(s.lessons.c.data)
        .where(and_(s.lessons.c.user_id == repo.learner_id, s.lessons.c.status == 'completed'))
        .order_by(s.lessons.c.started_at, s.lessons.c.id)).scalars()]
    ids = [lesson.id for lesson in completed]
    activated = roadmap.activation_at is None
    if activated:
        # The durable migration timestamp survives a failed/lazy first refresh.
        roadmap.activation_at = aware(db.execute(select(s.versions.c.applied_at)
            .where(s.versions.c.version == 4)).scalar_one())
        roadmap.baseline_ids = [lesson.id for lesson in completed
            if lesson.completed_at is None or aware(lesson.completed_at) <= roadmap.activation_at]
        # Preserve pre-fix checkpoints. Attach to this cycle only when their
        # completion accounting already began after migration activation.
        if not roadmap.baseline_ids:
            for feedback in roadmap.feedback:
                feedback.activation_at = roadmap.activation_at
    context = PlanningContext.model_validate({'goal': profile.goal, 'interests': profile.interests, 'situations': profile.target_situations,
        'accents': profile.accent_preferences.model_dump(mode='json'),
        'comprehension': profile.listening_profile.model_dump(), 'difficulty': profile.difficulty.model_dump(),
        'focus': profile.focus, 'onboarding_status': profile.onboarding_status,
        'recent_adaptations': list(db.execute(select(s.events.c.data).select_from(s.events.join(s.attempts, s.events.c.attempt_id == s.attempts.c.id).join(s.exercises, s.attempts.c.exercise_id == s.exercises.c.id))
            .where(s.exercises.c.user_id == repo.learner_id).order_by(s.events.c.created_at.desc(), s.events.c.id.desc()).limit(6)).scalars())})
    if activated or ids != roadmap.completed_ids or context != roadmap.context or len(roadmap.upcoming) != 3:
        roadmap.completed_ids = ids
        roadmap.context = context
        roadmap.pending = None
        roadmap.upcoming = outlines(profile, len(ids), roadmap.feedback, context)
        persist(repo, db, roadmap)
    return roadmap

def persist(repo, db, roadmap):
    roadmap.revision += 1
    repo.upsert(db, s.roadmaps, {'user_id': repo.learner_id, 'revision': roadmap.revision,
        'data': roadmap.model_dump(mode='json')}, ['user_id'])

def due(roadmap):
    count = qualifying_count(roadmap)
    milestone = count // 3 * 3
    return milestone if milestone and not any(item.after_lesson == milestone and item.activation_at == roadmap.activation_at for item in roadmap.feedback) else None

def access(repo, revision=None, pending=None, feedback=None, claim_lesson=None):
    with repo.transaction(planner=True) as db:
        row = db.execute(select(s.profiles.c.data).where(s.profiles.c.user_id == repo.learner_id).with_for_update()).scalar_one()
        profile = LearnerProfile.model_validate(row)
        roadmap = reconcile(repo, db, profile)
        if claim_lesson is not None:
            milestone = due(roadmap)
            if (not milestone or qualifying_count(roadmap) != milestone
                    or milestone in roadmap.offered_checkins
                    or not roadmap.completed_ids or roadmap.completed_ids[-1] != claim_lesson
                    or profile.onboarding_status != 'complete'
                    or db.execute(select(s.lessons.c.id).where(and_(s.lessons.c.user_id == repo.learner_id,
                        s.lessons.c.status != 'completed'))).first() is not None):
                return None
            roadmap.offered_checkins.append(milestone)
            persist(repo, db, roadmap)
            return roadmap
        if revision is not None:
            if roadmap.revision != revision or not due(roadmap):
                raise ValueError('Your plan changed. Reload the saved plan.')
            if profile.onboarding_status != 'complete' or db.execute(select(s.lessons.c.id).where(and_(
                    s.lessons.c.user_id == repo.learner_id, s.lessons.c.status != 'completed'))).first():
                raise ValueError('Finish or resume your lesson before this check-in.')
            if feedback is not None:
                roadmap.feedback.append(PlannerFeedback(activation_at=roadmap.activation_at, after_lesson=due(roadmap), **feedback))
                roadmap.pending = None
                roadmap.upcoming = outlines(profile, len(roadmap.completed_ids), roadmap.feedback, roadmap.context)
            else:
                roadmap.pending = pending
            persist(repo, db, roadmap)
        return roadmap
