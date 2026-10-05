import re
from app.models import ExerciseContent, EvaluationJudgments, Evaluation


def expected_units(exercise: ExerciseContent) -> dict[str, list[str]]:
    return {'main_idea': [exercise.main_idea], 'details': exercise.important_details,
            'vocabulary': [f'{v.phrase}: {v.meaning_in_context}' for v in exercise.vocabulary_items],
            'inference': exercise.inference_points}


def score_judgments(exercise: ExerciseContent, judgments: EvaluationJudgments, learner_transcript: str | None = None) -> Evaluation:
    expected = expected_units(exercise)
    required = {(dimension, i) for dimension, units in expected.items() for i in range(len(units))}
    received = [(u.dimension, u.index) for u in judgments.units]
    if len(set(received)) != len(received) or set(received) != required:
        raise ValueError('Evaluation must cover every information unit exactly once')
    for unit in judgments.units:
        if unit.status in ('understood', 'partial', 'misunderstood') and not unit.evidence.strip():
            raise ValueError('Non-missed judgments require learner evidence')
        if learner_transcript is not None and unit.status != 'missed':
            normalize = lambda text: re.sub(r'[^\w]+', ' ', text.casefold()).strip()
            evidence = normalize(unit.evidence)
            if not evidence or evidence not in normalize(learner_transcript):
                raise ValueError('Evaluation cited evidence absent from learner transcript')
    values = {'understood': 1, 'partial': .5, 'missed': 0, 'misunderstood': 0}
    scores = {dimension: sum(values[u.status] for u in judgments.units if u.dimension == dimension) / len(units)
              for dimension, units in expected.items()}
    overall = round(scores['main_idea']*.35 + scores['details']*.4 + scores['vocabulary']*.15 + scores['inference']*.1, 4)
    lists = {'understood': [], 'missed': [], 'misunderstood': []}
    for u in judgments.units:
        bucket = 'missed' if u.status == 'partial' else u.status
        label = expected[u.dimension][u.index]
        lists[bucket].append(f'Partly understood: {label}' if u.status == 'partial' else label)
    return Evaluation(**scores, overall=overall, **lists, feedback=judgments.feedback, units=judgments.units)
