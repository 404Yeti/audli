"""Application-owned evidence coverage, bounded clarification and final adaptation."""
import re
from uuid import uuid4
from app.adaptive import adapt, DIMENSIONS
from app.evaluation import expected_units
from app.models import (Adaptation, EvidenceAssessment, EvidenceEvaluation, Evaluation,
                        Conversation, Followup)

WEIGHTS = {'main_idea': .35, 'details': .4, 'vocabulary': .15, 'inference': .1}
MAX_FOLLOWUPS = 2


def validate_evidence(exercise, assessment: EvidenceAssessment, texts: list[str]):
    required = {(dim, i) for dim, units in expected_units(exercise).items() for i in range(len(units))}
    received = [(unit.dimension, unit.index) for unit in assessment.units]
    if len(set(received)) != len(received) or set(received) != required:
        raise ValueError('Evidence assessment must cover every information unit exactly once')
    normalize = lambda text: re.sub(r'[^\w]+', ' ', text.casefold()).strip()
    sources = [normalize(text) for text in texts]
    for unit in assessment.units:
        if unit.status == 'insufficient_evidence':
            if unit.evidence or not unit.question.strip():
                raise ValueError('Insufficient evidence needs a question and no invented learner quote')
        else:
            evidence = normalize(unit.evidence)
            if not evidence or not any(evidence in source for source in sources):
                raise ValueError('Evidence must be grounded in a learner turn')
            if unit.question:
                raise ValueError('Only insufficient evidence should request a follow-up')


def choose_followup(conversation: Conversation) -> Followup | None:
    if len(conversation.followups) >= MAX_FOLLOWUPS:
        return None
    asked = {(question.dimension, question.index) for question in conversation.followups}
    for dimension in DIMENSIONS:
        for unit in conversation.assessment.units:
            if unit.dimension == dimension and unit.status == 'insufficient_evidence' and (unit.dimension, unit.index) not in asked:
                return Followup(id=str(uuid4()), dimension=dimension, index=unit.index, question=unit.question)
    return None


def final_evaluation(exercise, assessment: EvidenceAssessment) -> EvidenceEvaluation:
    scores, states = {}, {}
    values = {'demonstrated': 1, 'partially_demonstrated': .5, 'misunderstood': 0}
    understood, unknown, wrong = [], [], []
    expected = expected_units(exercise)
    observed_weight = 0
    for dimension in DIMENSIONS:
        units = [unit for unit in assessment.units if unit.dimension == dimension]
        observed = [values[unit.status] for unit in units if unit.status in values]
        scores[dimension] = sum(observed) / len(observed) if observed else None
        observed_weight += WEIGHTS[dimension] * len(observed) / len(units)
        statuses = {unit.status for unit in units}
        states[dimension] = ('insufficient_evidence' if not observed else
                             'misunderstood' if 'misunderstood' in statuses else
                             'demonstrated' if statuses == {'demonstrated'} else 'partially_demonstrated')
        for unit in units:
            label = expected[dimension][unit.index]
            if unit.status == 'insufficient_evidence': unknown.append(label)
            elif unit.status == 'misunderstood': wrong.append(label)
            else: understood.append(('Partly demonstrated: ' if unit.status == 'partially_demonstrated' else '') + label)
    available_weight = sum(WEIGHTS[dimension] for dimension in DIMENSIONS if scores[dimension] is not None)
    overall = (round(sum(scores[dimension] * WEIGHTS[dimension] for dimension in DIMENSIONS
                         if scores[dimension] is not None) / available_weight, 4) if available_weight else None)
    return EvidenceEvaluation(**scores, overall=overall, coverage=round(observed_weight, 4), dimensions=states,
                              units=assessment.units, understood=understood, insufficient_evidence=unknown,
                              misunderstood=wrong, feedback=assessment.feedback)


def adapt_final(profile, evaluation: EvidenceEvaluation, settings):
    if not evaluation.insufficient_evidence:
        # Same V0.1 deterministic policy; only fully evidenced final results enter it.
        legacy = Evaluation(main_idea=evaluation.main_idea, details=evaluation.details,
            vocabulary=evaluation.vocabulary, inference=evaluation.inference, overall=evaluation.overall,
            understood=evaluation.understood, missed=[], misunderstood=evaluation.misunderstood,
            feedback=evaluation.feedback, units=[])
        updated, event = adapt(profile, legacy, settings)
        event.policy_version = 'v0.2-evidence'
        return updated, event
    # Unknown does not become failure or justify a harder clip. Update only fully observed dimensions.
    updated = profile.model_copy(deep=True)
    weight = 1 / (profile.completed_attempts + 1) if profile.completed_attempts < 3 else .3
    unknown = {unit.dimension for unit in evaluation.units if unit.status == 'insufficient_evidence'}
    for dimension in DIMENSIONS:
        if dimension not in unknown:
            value = getattr(evaluation, dimension)
            previous = getattr(profile.listening_profile, dimension)
            setattr(updated.listening_profile, dimension, round(previous * (1-weight) + value * weight, 4))
    updated.completed_attempts += 1
    updated.focus = next(dimension for dimension in DIMENSIONS if dimension in unknown)
    event = Adaptation(previous_score=evaluation.overall, decision='same', changed_variable=None,
        old_value=None, new_value=None, reason='Evidence remains incomplete after bounded clarification; keep difficulty stable.',
        focus=updated.focus, old_difficulty=profile.difficulty, new_difficulty=profile.difficulty,
        policy_version='v0.2-evidence', edge_low=settings.edge_low, edge_high=settings.edge_high)
    return updated, event


def next_message(event) -> str:
    if event.changed_variable:
        messages = {
            ('speech_rate', 'harder'): "I'll make the next clip a little faster.",
            ('speech_rate', 'easier'): "I'll slow the next clip down a little.",
            ('duration_seconds', 'harder'): "Let's try a slightly longer clip next.",
            ('duration_seconds', 'easier'): "Let's try a shorter clip next.",
            ('vocabulary_level', 'harder'): "Let's try some slightly more challenging words next.",
            ('vocabulary_level', 'easier'): "I'll use more familiar words in the next clip.",
            ('information_density', 'harder'): "Let's try catching a few more details next.",
            ('information_density', 'easier'): "I'll give you fewer details to follow next time.",
        }
        return messages[(event.changed_variable, event.decision)]
    return "Let's stay at this level for another one."
