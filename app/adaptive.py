from app.config import Settings
from app.models import Adaptation, Difficulty, Evaluation, LearnerProfile

DIMENSIONS = ('main_idea', 'details', 'vocabulary', 'inference')
LEVELS = ['A2', 'B1', 'B2', 'C1']
BOUNDS = {'speech_rate': (.6, 1.15, .05), 'duration_seconds': (30, 90, 5), 'information_density': (1, 3, 1)}


def adapt(profile: LearnerProfile, evaluation: Evaluation, settings: Settings):
    """Pure policy. Stable scores or saturated bounds change zero variables."""
    weakest = min(DIMENSIONS, key=lambda key: getattr(evaluation, key))
    score = evaluation.overall
    old = profile.difficulty.model_copy(deep=True)
    new = old.model_copy(deep=True)
    if score >= settings.edge_high:
        direction, magnitude = 1, 1
        reason = f'Overall comprehension reached {settings.edge_high:.0%}; increase slightly.'
        order = ['speech_rate', 'duration_seconds', 'vocabulary_level', 'information_density']
    elif score >= settings.edge_low:
        direction, magnitude = 0, 0
        reason = f'Within the {settings.edge_low:.0%}–{settings.edge_high:.0%} learning edge; reinforce {weakest}.'
        order = []
    else:
        direction = -1
        magnitude = 1 if score >= settings.low_score else 2
        reason = f'Overall comprehension below {settings.edge_low:.0%}; ease one variable and reinforce {weakest}.'
        preferred = {'main_idea': 'duration_seconds', 'details': 'information_density', 'vocabulary': 'vocabulary_level', 'inference': 'information_density'}[weakest]
        order = list(dict.fromkeys([preferred, 'speech_rate', 'duration_seconds', 'vocabulary_level', 'information_density']))
    changed = None
    for key in order:
        value = getattr(old, key)
        if key == 'vocabulary_level':
            candidate = LEVELS[max(0, min(len(LEVELS)-1, LEVELS.index(value) + direction))]
        else:
            lo, hi, step = BOUNDS[key]
            candidate = round(max(lo, min(hi, value + direction * magnitude * step)), 2)
            if key != 'speech_rate':
                candidate = int(candidate)
        if candidate != value:
            new = Difficulty.model_validate({**old.model_dump(), key: candidate})
            changed = key
            break
    if direction and changed is None:
        reason += ' All adjustable variables are at their allowed bound; maintain difficulty.'
    updated = profile.model_copy(deep=True)
    weight = 1 / (profile.completed_attempts + 1) if profile.completed_attempts < 3 else .3
    for key in ('overall', *DIMENSIONS):
        previous = getattr(profile.listening_profile, key)
        setattr(updated.listening_profile, key, round(previous * (1-weight) + getattr(evaluation, key) * weight, 4))
    updated.completed_attempts += 1
    updated.difficulty = new
    updated.focus = weakest
    event = Adaptation(previous_score=score, decision='same' if changed is None else ('harder' if direction > 0 else 'easier'),
                       changed_variable=changed, old_value=getattr(old, changed) if changed else None,
                       new_value=getattr(new, changed) if changed else None, reason=reason, focus=weakest,
                       old_difficulty=old, new_difficulty=new, edge_low=settings.edge_low, edge_high=settings.edge_high)
    return updated, event
