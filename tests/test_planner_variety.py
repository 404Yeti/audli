"""New outline variety and non-destructive preservation; no live providers."""
import pytest
from app.models import LearnerProfile
from app.planner import outlines, PlannerFeedback
from app.storage.planner import persist
from test_persistence import repository_factory
from test_planner import ready, complete


@pytest.mark.parametrize('focus', ['main_idea','details','vocabulary','inference'])
@pytest.mark.parametrize('interests', [[], ['travel'], ['science','travel']])
def test_new_block_has_distinct_contexts_and_tasks_without_changing_level(focus, interests):
    profile = LearnerProfile(focus=focus, interests=interests)
    before = profile.model_copy(deep=True)
    plan = outlines(profile, 0, [], None)
    assert len({x.topic for x in plan}) == len({x.objective for x in plan}) == 3
    assert [x.topic.split(': ')[-1] for x in plan] == ['a short update','practical instructions','a personal story']
    assert all(x.focus == focus and x.difficulty == profile.difficulty for x in plan)
    assert all(profile.goal in x.objective for x in plan)
    assert profile == before and outlines(profile, 0, [], None) == plan


def test_feedback_personalizes_each_distinct_context_without_changing_difficulty():
    profile = LearnerProfile(interests=['science'])
    feedback = [PlannerFeedback(after_lesson=3,status='answered',interests=['travel'],focus='inference')]
    plan = outlines(profile, 3, feedback, None)
    assert all(x.topic.startswith('travel: ') and x.focus=='inference' for x in plan)
    assert len({x.objective for x in plan}) == 3
    assert all(x.difficulty == profile.difficulty for x in plan)


def test_existing_saved_outlines_are_not_refreshed_by_generator_upgrade(repository_factory):
    repo = repository_factory(); ready(repo)
    saved = repo.roadmap()
    # Representative pre-polish persisted block, with duplicate objectives.
    for lesson in saved.upcoming:
        lesson.title = 'Science: important details'; lesson.topic = 'science'
        lesson.objective = 'Practice important details for work.'
    with repo.transaction(planner=True) as db: persist(repo, db, saved)
    history = repo.accent_lesson_history(); profile = repo.profile()
    assert repo.roadmap() == saved
    assert repo.roadmap() == saved  # Repeated reads do not write or migrate plans.
    assert repo.profile() == profile and repo.accent_lesson_history() == history
    complete(repo)  # Existing natural completion reconciliation generates a block.
    updated = repo.roadmap()
    assert len({x.topic for x in updated.upcoming}) == len({x.objective for x in updated.upcoming}) == 3
    assert repo.profile() == profile


def test_rolling_refresh_advances_the_context_actually_selected_at_lesson_start():
    profile = LearnerProfile(interests=['science'])
    blocks = [outlines(profile, count, [], None) for count in range(3)]
    assert len({block[0].topic for block in blocks}) == 3
    assert len({block[0].objective for block in blocks}) == 3
    # The next selected outline retains the context previously shown in slot two.
    assert blocks[0][1] == blocks[1][0]
    assert blocks[1][1] == blocks[2][0]
