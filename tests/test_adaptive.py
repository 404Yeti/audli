import pytest
from app.adaptive import adapt
from app.config import Settings
from app.models import Difficulty, Evaluation, LearnerProfile


def evaluation(score, **parts):
    return Evaluation(main_idea=parts.get('main_idea',score), details=parts.get('details',score), vocabulary=parts.get('vocabulary',score),
        inference=parts.get('inference',score), overall=score, understood=[], missed=[], misunderstood=[], feedback='Practice feedback', units=[])

@pytest.mark.parametrize('score,decision,changed,duration', [(.54,'easier',1,35),(.55,'easier',1,40),(.69,'easier',1,40),(.70,'same',0,45),(.84,'same',0,45),(.85,'harder',1,45)])
def test_boundaries(score,decision,changed,duration):
    profile = LearnerProfile()
    updated,event = adapt(profile,evaluation(score),Settings(_env_file=None))
    differences = [k for k,v in profile.difficulty.model_dump().items() if v != updated.difficulty.model_dump()[k]]
    assert len(differences) == changed
    assert event.decision == decision
    assert updated.difficulty.duration_seconds == duration
    assert event.reason and event.previous_score == score
    assert updated.completed_attempts == 1
    assert updated.listening_profile.overall == score
    assert profile.completed_attempts == 0
    if changed:
        assert event.changed_variable == differences[0]
        assert event.old_value != event.new_value

@pytest.mark.parametrize('score', [0,.54,.55,.69,.7,.84,.85,1])
@pytest.mark.parametrize('difficulty', [Difficulty(),Difficulty(speech_rate=.6,duration_seconds=30,vocabulary_level='A2',information_density=1),Difficulty(speech_rate=1.15,duration_seconds=90,vocabulary_level='C1',information_density=3)])
def test_ranges_and_single_change(score,difficulty):
    profile=LearnerProfile(difficulty=difficulty)
    updated,event=adapt(profile,evaluation(score),Settings(_env_file=None))
    Difficulty.model_validate(updated.difficulty.model_dump())
    assert sum(v != updated.difficulty.model_dump()[k] for k,v in difficulty.model_dump().items()) <= 1
    assert event.old_difficulty == difficulty
    assert event.new_difficulty == updated.difficulty
    assert updated.listening_profile.natural_speed is None


def test_targets_weakest_in_edge():
    updated,event=adapt(LearnerProfile(),evaluation(.75,details=.3),Settings(_env_file=None))
    assert event.focus == updated.focus == 'details'
    assert event.changed_variable is None


def test_baseline_running_mean_then_smoothing():
    profile=LearnerProfile()
    for score in (.5,.7,.9):
        profile,_=adapt(profile,evaluation(score),Settings(_env_file=None))
    assert profile.listening_profile.overall == pytest.approx(.7,abs=.001)
    profile,_=adapt(profile,evaluation(1),Settings(_env_file=None))
    assert profile.listening_profile.overall == pytest.approx(.79,abs=.001)


def test_configurable_edge():
    _,event=adapt(LearnerProfile(),evaluation(.8),Settings(edge_low=.6,edge_high=.8,_env_file=None))
    assert event.decision=='harder'
    with pytest.raises(ValueError):
        Settings(edge_low=.9,edge_high=.8,_env_file=None)
