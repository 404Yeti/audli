import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.evaluation import expected_units, score_judgments
from app.models import ExerciseContent, Evaluation, EvaluationJudgments

CASES=json.loads(Path('tests/fixtures/evaluator_cases.json').read_text())

def judgments(exercise,case):
    keys=[(dimension,i) for dimension,units in expected_units(exercise).items() for i in range(len(units))]
    return EvaluationJudgments(units=[{'dimension':dimension,'index':i,'status':status,
        'evidence':case['response'] if status!='missed' else ''} for (dimension,i),status in zip(keys,case['statuses'])],
        feedback='Comprehension feedback.',transcription_concern=False,concern_reason='')

@pytest.mark.parametrize('case',CASES,ids=[c['name'] for c in CASES])
def test_evaluator_rubric_fixtures(exercise,case):
    result=score_judgments(exercise,judgments(exercise,case))
    lo,hi=case['overall_range']
    assert lo<=result.overall<=hi
    if case['name']=='significant_misunderstanding':
        assert result.misunderstood and result.missed
    if case['name']=='poor':
        assert not result.misunderstood


def test_grammar_has_no_score_penalty(exercise):
    excellent=score_judgments(exercise,judgments(exercise,CASES[0]))
    broken=score_judgments(exercise,judgments(exercise,CASES[4]))
    assert excellent.overall==broken.overall==1


def test_invalid_coverage_rejected(exercise):
    original=judgments(exercise,CASES[0])
    for units in [original.units[:-1],original.units+[original.units[0]]]:
        with pytest.raises(ValueError):
            score_judgments(exercise,original.model_copy(update={'units':units}))


def test_evidence_required(exercise):
    original=judgments(exercise,CASES[0])
    original.units[0].evidence=''
    with pytest.raises(ValueError):
        score_judgments(exercise,original)

@pytest.mark.parametrize('field,value',[('script','tiny'),('speech_rate',4),('vocabulary_level','C2'),('important_details',[]),('extra_field','anything')])
def test_exercise_schema_rejects_invalid(exercise,field,value):
    with pytest.raises(ValidationError):
        ExerciseContent.model_validate({**exercise.model_dump(),field:value})

@pytest.mark.parametrize('value',[-.01,1.01,float('nan'),float('inf')])
def test_scores_normalized(exercise,value):
    result=score_judgments(exercise,judgments(exercise,CASES[0]))
    with pytest.raises(ValidationError):
        Evaluation.model_validate({**result.model_dump(),'overall':value})


def test_arbitrary_prose_and_unknown_status_rejected():
    with pytest.raises(ValidationError):
        EvaluationJudgments.model_validate_json('Learner did pretty well!')
    with pytest.raises(ValidationError):
        EvaluationJudgments.model_validate({'units':[{'dimension':'details','index':0,'status':'good','evidence':'anything'}],
            'feedback':'Good','transcription_concern':False,'concern_reason':''})


def test_hallucinated_learner_evidence_rejected(exercise):
    original=judgments(exercise,CASES[0])
    original.units[0].evidence='I understood absolutely everything perfectly.'
    with pytest.raises(ValueError):
        score_judgments(exercise,original,CASES[0]['response'])


def test_actual_learner_evidence_passes(exercise):
    assert score_judgments(exercise,judgments(exercise,CASES[4]),CASES[4]['response']).overall==1
