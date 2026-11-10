"""Audio-first grounding: a speaker's name may appear in the exercises only if somebody actually says it in the audio.

Regression for the first controlled Daily Challenge (lesson "A Surprising Move", 2026-11-10): the narrator's name "Alex" lived only in the speaker metadata, but the
multiple-choice question ("What color boat did Alex pick?"), a sequencing item and an explanation used it, so a listener could not answer. The semantic review caught it
(and stays the final evidence-grounding check); these tests make the deterministic layer, and the writer's own repair loop, catch it first and for free.
"""
import pytest

from app.listening import daily_gates as gates, generation as g
from app.listening.domain import unspoken_name_message, unspoken_speaker_names
from app.listening.generation import FakeScriptWriter, Spec
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation import lab, start, run, view  # noqa: F401  (fixtures and helpers)

STORY = Spec(topic='a board game night', cefr='A2', accent='american', minutes=2, format='story', scenario='a first-person story told by one friend')
SCRIPT = ['Last Friday, I invited three friends to my house.', 'We wanted to have a board game night.', 'My friend Mia brought some snacks.',
          'Then Ben picked up a special card.', 'I picked the blue boat.', 'I found the answer on the last page.']


# ---- the pure rule -----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('where,text', [
    ('MCQ prompt', 'What color boat did Alex pick?'), ('MCQ option', "Alex's red boat"), ('MCQ explanation', 'Alex says, “I picked the blue boat.”'),
    ('spoken prompt', 'What did Alex do after the card?'), ('spoken key point', 'Alex opened the rule book.'), ('fill explanation', 'Alex found the answer on the last page.'),
    ('sequencing instruction', 'Put the events in the order Alex describes them.'), ('sequencing item', 'Alex opens the rule book again.'),
    ('sequencing explanation', 'First Mia found the rules, then Alex checked them.'), ('listening goal', 'Listen for what Alex does.')])
def test_a_name_used_anywhere_in_the_exercises_but_never_spoken_is_flagged(where, text):
    assert unspoken_speaker_names(['Alex'], SCRIPT, ['An unrelated prompt', text]) == ['Alex'], where


def test_the_message_tells_the_writer_both_ways_to_fix_it_within_the_repair_loops_300_characters():
    message = unspoken_name_message(['Alex'])
    assert 'Alex' in message and 'says it' in message and 'aloud' in message and 'the speaker' in message and len(message) <= 300


@pytest.mark.parametrize('label,names,spoken,texts', [
    ('the name is spoken (self-introduction)', ['Alex'], ['Hi, I am Alex.', *SCRIPT], ['What color boat did Alex pick?']),
    ('the name is spoken (addressed)', ['Priya'], ['Thanks, Priya.', *SCRIPT], ["What did Priya's friend order?"]),
    ('the possessive is spoken', ['Alex'], ["That is Alex's boat.", *SCRIPT], ['Which boat is Alex\'s?']),
    ('exercises say "the speaker" instead', ['Alex'], SCRIPT, ['What color boat did the speaker pick?', 'Put the events in the order the narrator tells them.']),
    ('other characters named in the script are fine', ['Alex'], SCRIPT, ['What did Mia bring?', 'Who picked up the special card?', 'Ben took a star.']),
    ('wrong-answer options may name anyone', ['Alex'], SCRIPT, ['Who brought the snacks?', 'Mia', 'Leo', 'Sofia', 'Ben']),
    ('a full name is established by any part of it', ['Dr. Alex Tan'], ['Hello, I am Alex.', *SCRIPT], ['What did Alex Tan decide?']),
    ('an ordinary word that happens to be a name is not a use', ['Will'], SCRIPT, ['What will happen next?', 'Which boat will they choose?']),
    ('no usable name (too short / empty)', ['Al', ''], SCRIPT, ['Al did it']),
    ('names are never compared inside longer words', ['Ann'], SCRIPT, ['What is the Annual game night about?'])])
def test_ordinary_lessons_stay_valid(label, names, spoken, texts):
    assert unspoken_speaker_names(names, spoken, texts) == [], label


def test_every_speaker_is_checked_independently_and_in_order():
    assert unspoken_speaker_names(['Sam', 'Priya'], ['Thanks, Priya.'], ['What did Sam order?', 'What did Priya say?']) == ['Sam']
    assert unspoken_speaker_names(['Sam', 'Priya'], ['Hello there.'], ['What did Sam order?', 'What did Priya say?']) == ['Sam', 'Priya']


# ---- the assembler (what the writer's repair loop calls) -------------------------------------------------------------------------------------------

def flawed(raw, where):
    raw['speakers'] = [{**raw['speakers'][0], 'name': 'Alex'}]
    if where == 'mcq prompt':
        raw['mcq']['prompt'] = 'What color boat did Alex pick?'
    elif where == 'mcq explanation':
        raw['mcq']['explanation'] = 'Alex says she wants a large latte.'
    elif where == 'mcq option':
        raw['mcq']['options'][0]['text'] = "Alex's tea"
    elif where == 'sequencing prompt':
        raw['sequence']['prompt'] = 'Put the events in the order Alex describes them.'
    elif where == 'sequencing item':
        raw['sequence']['events'][0]['text'] = 'Alex orders a latte'
    elif where == 'sequencing explanation':
        raw['sequence']['explanation'] = 'Alex tells them in this order.'
    elif where == 'fill explanation':
        raw['fill']['explanation'] = 'Alex says the muffin is two dollars.'
    elif where == 'spoken key point':
        raw['spoken']['points'][0]['text'] = 'Alex wanted a latte with oat milk'
    return raw


WHERE = ['mcq prompt', 'mcq explanation', 'mcq option', 'sequencing prompt', 'sequencing item', 'sequencing explanation', 'fill explanation', 'spoken key point']


@pytest.mark.parametrize('where', WHERE)
def test_the_assembler_refuses_a_script_whose_exercises_name_an_unspoken_speaker(where):
    raw = flawed(FakeScriptWriter().write(STORY, g.estimate_chars(2), None), where)
    with pytest.raises(ValueError, match='Alex') as refused:
        g.assemble(STORY, raw, 'request-1', g.estimate_chars(2))
    assert 'aloud' in str(refused.value)


@pytest.mark.parametrize('where', WHERE)
def test_the_same_script_is_accepted_once_the_speaker_says_the_name(where):
    raw = flawed(FakeScriptWriter().write(STORY, g.estimate_chars(2), None), where)
    raw['sentences'][0]['text'] = 'Hello, I am Alex and I work at the coffee shop on Fifth Avenue.'
    assert g.assemble(STORY, raw, 'request-1', g.estimate_chars(2)).speakers[0].name == 'Alex'


def test_the_stock_fake_lessons_and_ordinary_named_characters_are_unaffected():
    for spec in (STORY, Spec(topic='ordering coffee', cefr='B1', accent='american', minutes=2, format='dialogue')):
        assert g.assemble(spec, FakeScriptWriter().write(spec, g.estimate_chars(2), None), 'r', g.estimate_chars(2)).title
    raw = FakeScriptWriter().write(STORY, g.estimate_chars(2), None)
    raw['speakers'] = [{**raw['speakers'][0], 'name': 'Alex'}]
    raw['sentences'][0]['text'] = 'Hello, my friend Mia and I are at the coffee shop on Fifth Avenue.'
    raw['mcq']['prompt'] = 'What does Mia order to drink?'                          # a character named in the script, not the (unspoken) narrator
    assert g.assemble(STORY, raw, 'r', g.estimate_chars(2)).exercise.prompt == 'What does Mia order to drink?'


# ---- the repair loop and the money: no audio is paid for until the script is grounded ------------------------------------------------------------------

def test_the_writer_is_told_and_corrects_it_before_any_audio_is_synthesised(lab):
    writer = FakeScriptWriter(mutate=lambda raw, call: flawed(raw, 'mcq prompt') if call == 1 else None)
    d = lab.deps(writer=writer)
    request_id = start(lab, d, spec=STORY)
    assert run(lab, d) == 'done' and writer.calls == 2                                  # the existing two-call budget: no extra provider call for this rule
    assert 'Alex' in writer.feedback[1] and 'aloud' in writer.feedback[1]               # the second call was told exactly what to fix
    assert view(lab, lab.owner, request_id)['phase'] == 'ready'
    sentences = len(FakeScriptWriter().write(STORY, g.estimate_chars(2), None)['sentences'])
    assert len(d.speech.inner.calls) == sentences                                       # one build of the corrected script; the flawed one was never voiced


def test_a_script_that_stays_ungrounded_is_refused_with_no_audio_paid_for(lab):
    always = FakeScriptWriter(mutate=lambda raw, call: flawed(raw, 'mcq prompt'))
    d = lab.deps(writer=always)
    request_id = start(lab, d, spec=STORY)
    assert run(lab, d) == 'failed' and always.calls == 2
    seen = view(lab, lab.owner, request_id)
    assert seen['phase'] == 'failed' and 'quality checks' in seen['failure'] and seen['lesson_id'] is None
    assert d.speech.inner.calls == [] and d.speech.spent == 0


# ---- the daily gate (defence in depth) and the semantic reviewer stays as it was --------------------------------------------------------------------------------

ALEX_SCRIPT = [
    'Last Friday, I invited three friends to my house.', 'We wanted to have a board game night.', 'I put the game on the kitchen table.', 'My friend Mia brought some snacks.',
    'There were apples, chips, and chocolate.', 'Leo made tea for everyone.', 'Then we sat down and opened the box.', 'Inside, we found cards, small trees, and four wooden boats.',
    'The board showed an island with a lake.', 'Our goal was to collect five golden stars.', 'I had never played this game before.', 'Mia found the rules under the board.',
    'She read the first page slowly.', 'We listened and looked at the pictures.', 'Each player chose a different colored boat.', 'I picked the blue boat.',
    'We learned how to move across the lake.', 'We also learned how to get stars.', 'At first, the rules seemed easy.', 'We started the game after our tea.',
    'Leo moved first and found a star.', 'I moved next but found only a tree.', 'Mia found a card near the lake.', 'Our other friend, Ben, had no stars yet.',
    'After a few turns, Mia had three stars.', 'I had two, and Leo had one.', 'Ben still had none.', 'Then Ben picked up a special card.',
    'It let him take a star from another player.', "He took one of Mia's stars.", 'We were all surprised by his move.', 'Mia thought the move was not fair.',
    'Ben said the card was part of the game.', 'They argued for a few minutes.', 'I opened the rule book again.', 'I found the answer on the last page.',
    'The rules said Ben could take the star.', 'Mia was still unhappy, but she accepted it.', 'We all took a short break for snacks.', 'Then we returned to the game.',
    'Soon, I found another star near a tree.', 'Mia got three stars on her next turn.', 'In the end, Mia collected five stars and won.', 'We clapped and put the game away.',
    'Next time, we will read every page first.']


def alex_snapshot(script=ALEX_SCRIPT, **exercise_overrides):
    """The exact lesson the first controlled challenge produced (script and exercises as stored), optionally with the exercises or script repaired."""
    ex = {
        'multiple_choice': {'prompt': 'What color boat did Alex pick?', 'options': [{'id': 'a', 'text': 'Red'}, {'id': 'b', 'text': 'Blue'}, {'id': 'c', 'text': 'Green'}, {'id': 'd', 'text': 'Yellow'}],
                            'answer': {'correct_option_id': 'b', 'explanation': 'Alex says, “I picked the blue boat.”'}},
        'spoken': {'prompt': 'What happened after Ben picked up the special card?', 'options': [],
                   'answer': {'points': [{'id': 'p1', 'text': "Ben took one of Mia's stars."}, {'id': 'p2', 'text': 'Mia and Ben argued about the move.'},
                                         {'id': 'p3', 'text': 'The rules said Ben could take the star.'}]}},
        'fill_blank': {'prompt': 'I found the answer on the ___.', 'options': [], 'answer': {'accepted': ['last page'], 'explanation': 'Alex found the answer on the last page.'}},
        'sequencing': {'prompt': 'Put the events in the order Alex describes them.',
                       'options': [{'id': 'e4', 'text': 'Mia wins the game.'}, {'id': 'e1', 'text': 'Mia finds the rules.'}, {'id': 'e2', 'text': 'Ben picks a special card.'},
                                   {'id': 'e3', 'text': 'Alex opens the rule book again.'}],
                       'answer': {'order': ['e1', 'e2', 'e3', 'e4'], 'explanation': 'Mia found the rules first. Later, Ben picked a card, Alex checked the rules, and Mia won.'}}}
    for kind, patch in exercise_overrides.items():
        ex[kind] = {**ex[kind], **patch, 'answer': {**ex[kind]['answer'], **patch.get('answer', {})}}
    return gates.Snapshot('L', 'V', 'A Surprising Move', 'A board game night', 'A2', 'Listen for what happens when Ben takes a star.', 'h',
                          [{'id': 'narrator', 'name': 'Alex', 'role': 'Narrator', 'accent': 'japanese'}], list(script),
                          [{'kind': kind, **value} for kind, value in ex.items()], [])


def test_the_lesson_the_semantic_review_rejected_is_now_rejected_deterministically_and_for_that_reason_alone():
    result = gates.answer_consistency(alex_snapshot())
    assert not result.ok and len(result.problems) == 1 and 'Alex' in result.problems[0] and 'aloud' in result.problems[0]


def test_the_same_lesson_passes_when_the_name_is_spoken_or_the_exercises_stop_using_it():
    assert gates.answer_consistency(alex_snapshot(script=["Hi, I'm Alex.", *ALEX_SCRIPT])).ok
    repaired = alex_snapshot(multiple_choice={'prompt': 'What color boat did the speaker pick?', 'answer': {'explanation': 'The speaker says, “I picked the blue boat.”'}},
                             fill_blank={'answer': {'explanation': 'The speaker found the answer on the last page.'}},
                             sequencing={'prompt': 'Put the events in the order the speaker describes them.',
                                         'options': [{'id': 'e4', 'text': 'Mia wins the game.'}, {'id': 'e1', 'text': 'Mia finds the rules.'}, {'id': 'e2', 'text': 'Ben picks a special card.'},
                                                     {'id': 'e3', 'text': 'The speaker opens the rule book again.'}],
                                         'answer': {'explanation': 'Mia found the rules first. Later, Ben picked a card, the speaker checked the rules, and Mia won.'}})
    assert gates.answer_consistency(repaired).ok


def test_the_semantic_reviewer_is_unchanged_and_still_rejects_exactly_what_it_rejected():
    verdict = {'safe': True, 'coherent': False, 'level_ok': True, 'same_scenario_as': None,
               'issues': ["The script never identifies the narrator as Alex, so the question about Alex's boat color is not answerable from the script."]}
    result = gates.semantic_review(verdict, set())
    assert not result.ok and any('incoherent' in p for p in result.problems) and any('never identifies' in p for p in result.problems)
    assert gates.semantic_review({'safe': True, 'coherent': True, 'level_ok': True, 'same_scenario_as': None, 'issues': []}, set()).ok
