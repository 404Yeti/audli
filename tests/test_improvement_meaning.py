"""Layered meaning preservation for advisory Review coaching (AUD-77). Pure functions, no providers.

Rejection is the safe default: each case below states whether the deterministic layers (1-3) accept or reject.
Layer 4 (an independent provider comparison) is covered at the API level in test_review_improvement.py.
"""
import pytest
from pydantic import ValidationError

from app.improvement import (LanguageCoaching, Note, diff, eligibility, reject_reason, reliable, source_hash, view,
                             is_unchanged, available_state, changed_words)


def coaching(improved, *notes, meaning=True, adds=False):
    return LanguageCoaching(improved_text=improved, meaning_preserved=meaning, adds_information=adds,
        notes=[Note(kind=kind, before=before, after=after, why='A short reason.') for kind, before, after in notes])


def check(source, improved, *notes, **kwargs):
    return reject_reason(source, coaching(improved, *notes, **kwargs))


# ---------------------------------------------------------------------------------------------- accepted improvements
@pytest.mark.parametrize('source,improved,note', [
    ('She change the meeting to Monday because the team was sick', 'She changed the meeting to Monday because the team was sick', ('grammar', 'She change', 'She changed')),
    ('He don\'t know about the problem at all', 'He doesn\'t know about the problem at all', ('grammar', "don't", "doesn't")),
    ('There are too much people in the room today', 'There are too many people in the room today', ('grammar', 'too much people', 'too many people')),
    ('The new app is more fast than the old one', 'The new app is faster than the old one', ('grammar', 'more fast', 'faster')),
    ('He is more taller than me in my class', 'He is taller than me in my class', ('grammar', 'more taller', 'taller')),
    ('I go store yesterday and buy some milk', 'I went to the store yesterday and bought some milk', ('grammar', 'go store yesterday and buy', 'went to the store yesterday and bought')),
    ('I don\'t have no time for the long meeting', 'I don\'t have any time for the long meeting', ('grammar', 'no time', 'any time')),
    ('We was happy about the result of the test', 'We were happy about the result of the test', ('grammar', 'We was', 'We were')),
    ('Change meeting Monday for the team please', 'Changed the meeting Monday for the team please', ('grammar', 'Change meeting', 'Changed the meeting')),
    ('The manager moved the meeting by maya request', 'The manager moved the meeting by Maya request', ('grammar', 'maya request', 'Maya request')),
])
def test_legitimate_grammar_fixes_are_accepted(source, improved, note):
    assert check(source, improved, note) is None


def test_case_and_punctuation_only_changes_need_no_note_and_an_unchanged_answer_needs_none():
    assert check('the team fixed the database problem fast', 'The team fixed the database problem fast.') is None
    assert check('The team fixed the database problem fast', 'The team fixed the database problem fast') is None
    assert is_unchanged('The team fixed it', 'The team  fixed it ') and not is_unchanged('the team fixed it', 'The team fixed it.')


# ---------------------------------------------------------------------------------------------- negation
@pytest.mark.parametrize('source,improved,note', [
    ('I don\'t like the new schedule at all', 'I like the new schedule at all', ('grammar', "don't like", 'like')),
    ('I like the new schedule at work today', "I don't like the new schedule at work today", ('grammar', 'I like', "I don't like")),
    ('Nobody called the manager about the late report', 'Anybody called the manager about the late report', ('vocabulary', 'Nobody', 'Anybody')),
    ('The team never finished the report on time', 'The team finished the report on time', ('vocabulary', 'never finished', 'finished')),
    ('He can not join the call this morning', 'He can join the call this morning', ('grammar', 'can not', 'can')),
    ('We went without the new tool this week', 'We went with the new tool this week', ('vocabulary', 'without', 'with')),
])
def test_negation_added_or_removed_is_rejected(source, improved, note):
    assert check(source, improved, note) == 'negation_changed'


# ---------------------------------------------------------------------------------------------- modality
@pytest.mark.parametrize('source,improved,note', [
    ('The manager must approve the plan today', 'The manager can approve the plan today', ('vocabulary', 'must approve', 'can approve')),
    ('He said he will come tomorrow to the office', 'He said he would come tomorrow to the office', ('grammar', 'will come', 'would come')),
    ('We should send the report before Friday', 'We could send the report before Friday', ('vocabulary', 'should send', 'could send')),
    ('You have to sign the form at the desk', 'You can sign the form at the desk', ('vocabulary', 'have to sign', 'can sign')),
    ('She can\'t join the meeting this afternoon', 'She won\'t join the meeting this afternoon', ('vocabulary', "can't join", "won't join")),
    ('They might delay the release by two weeks', 'They will delay the release by two weeks', ('vocabulary', 'might delay', 'will delay')),
])
def test_modality_changes_are_rejected(source, improved, note):
    assert check(source, improved, note) in ('modality_changed', 'quantity_changed', 'certainty_changed')


# ---------------------------------------------------------------------------------------------- quantities
@pytest.mark.parametrize('source,improved,note', [
    ('They sold three tickets on the first day', 'They sold four tickets on the first day', ('vocabulary', 'three tickets', 'four tickets')),
    ('They sold 3 tickets on the first day', 'They sold 30 tickets on the first day', ('vocabulary', '3 tickets', '30 tickets')),
    ('Some people liked the change very much', 'All people liked the change very much', ('vocabulary', 'Some people', 'All people')),
    ('Most of the team liked the new office', 'Some of the team liked the new office', ('vocabulary', 'Most of', 'Some of')),
    ('Both managers approved the new plan', 'Every manager approved the new plan', ('vocabulary', 'Both managers', 'Every manager')),
    ('Few customers noticed the change today', 'Many customers noticed the change today', ('vocabulary', 'Few customers', 'Many customers')),
    ('She only needs one more day for it', 'She needs one more day for it', ('vocabulary', 'only needs', 'needs')),
    ('The tickets cost twenty dollars each', 'The tickets cost fifty dollars each', ('vocabulary', 'twenty', 'fifty')),
])
def test_quantity_and_number_changes_are_rejected(source, improved, note):
    assert check(source, improved, note) in ('quantity_changed', 'content_removed', 'content_added')


def test_equivalent_number_forms_are_not_a_change():
    assert check('They sold three tickets on the first day', 'They sold 3 tickets on the first day', ('grammar', 'three tickets', '3 tickets')) is None


# ---------------------------------------------------------------------------------------------- comparisons
@pytest.mark.parametrize('source,improved,note', [
    ('The new app is more reliable than the old one', 'The new app is less reliable than the old one', ('vocabulary', 'more reliable', 'less reliable')),
    ('The app is bigger than the old app today', 'The app is smaller than the old app today', ('vocabulary', 'bigger', 'smaller')),
    ('The app is bigger than the old app today', 'The app is faster than the old app today', ('vocabulary', 'bigger', 'faster')),
    ('The new office is bigger than the old office', 'The new office is bigger the old office', ('grammar', 'bigger than', 'bigger')),
    ('This option is better than the other one', 'This option is worse than the other one', ('vocabulary', 'better', 'worse')),
    ('Sales are higher than last month overall', 'Sales are lower than last month overall', ('vocabulary', 'higher', 'lower')),
])
def test_comparison_changes_are_rejected(source, improved, note):
    assert check(source, improved, note) in ('comparison_changed', 'content_removed')


# ---------------------------------------------------------------------------------------------- certainty
@pytest.mark.parametrize('source,improved,note', [
    ('Sales will probably go up next month for us', 'Sales will definitely go up next month for us', ('vocabulary', 'probably', 'definitely')),
    ('Maybe the team will finish before the weekend', 'Surely the team will finish before the weekend', ('vocabulary', 'Maybe', 'Surely')),
    ('I think the plan works for the whole team', 'The plan works for the whole team', ('grammar', 'I think the plan', 'The plan')),
    ('We always test the release before the launch', 'We sometimes test the release before the launch', ('vocabulary', 'always', 'sometimes')),
    ('He is sure that the update was safe enough', 'He is unsure that the update was safe enough', ('vocabulary', 'sure', 'unsure')),
    ('The report is certainly late again this week', 'The report is possibly late again this week', ('vocabulary', 'certainly', 'possibly')),
])
def test_certainty_changes_are_rejected(source, improved, note):
    assert check(source, improved, note) in ('certainty_changed', 'content_removed', 'content_added')


# ---------------------------------------------------------------------------------------------- causality and logic
@pytest.mark.parametrize('source,improved,note', [
    ('We delayed the release because the tests failed', 'We delayed the release so the tests failed', ('grammar', 'because', 'so')),
    ('The tests failed so we delayed the release', 'The tests failed because we delayed the release', ('grammar', 'so', 'because')),
    ('We will ship on Friday if the tests pass', 'We will ship on Friday unless the tests pass', ('grammar', 'if', 'unless')),
    ('The plan is good but the schedule is tight', 'The plan is good so the schedule is tight', ('grammar', 'but', 'so')),
    ('We delayed the release due to the tests', 'We delayed the release although the tests', ('grammar', 'due to', 'although')),
])
def test_causal_and_logical_connection_changes_are_rejected(source, improved, note):
    assert check(source, improved, note) in ('causality_changed', 'content_added', 'content_removed', 'comparison_changed')


def test_a_connective_swap_inside_the_same_logical_class_is_a_grammar_fix():
    assert check('Although it was cold, but we went outside', 'Although it was cold, we went outside', ('grammar', 'cold, but we', 'cold, we')) is None
    assert check('The team was late because of the traffic', 'The team was late because the traffic', ('grammar', 'because of the', 'because the')) is None


# ---------------------------------------------------------------------------------------------- named entities and facts
@pytest.mark.parametrize('source,improved,note', [
    ('Maya moved the meeting to Friday afternoon', 'Mia moved the meeting to Friday afternoon', ('vocabulary', 'Maya', 'Mia')),
    ('We moved it to Monday because Maya was sick', 'We moved it to Tuesday because Maya was sick', ('vocabulary', 'Monday', 'Tuesday')),
    ('The call with Acme starts at the office soon', 'The call with Apex starts at the office soon', ('vocabulary', 'Acme', 'Apex')),
    ('We flew to Paris for the big conference', 'We flew to London for the big conference', ('vocabulary', 'Paris', 'London')),
])
def test_names_places_and_dates_cannot_change(source, improved, note):
    assert check(source, improved, note) == 'entity_changed'


@pytest.mark.parametrize('source,improved,note', [
    ('The team fixed it quickly', 'The team fixed it quickly on Friday morning', ('vocabulary', 'quickly', 'quickly on Friday morning')),
    ('The team fixed the bug', 'The team fixed the bug quickly', ('vocabulary', 'the bug', 'the bug quickly')),
    ('The customer called about the invoice', 'The angry customer called about the invoice', ('vocabulary', 'The customer', 'The angry customer')),
    ('The team fixed the bug and tested it again', 'The team fixed the bug', ('vocabulary', 'bug and tested it again', 'bug')),
])
def test_added_or_removed_facts_are_rejected(source, improved, note):
    assert check(source, improved, note) in ('content_added', 'content_removed', 'length_changed', 'entity_changed')


def test_a_synonym_swap_of_a_noun_passes_the_lexical_layers_by_design_and_needs_the_independent_check():
    """Documented limit: only layer 4 can tell 'database' from 'network'. The API requires both layers to pass."""
    assert check('The team fixed the database problem fast yesterday', 'The team repaired the network problem fast yesterday',
                 ('vocabulary', 'fixed the database', 'repaired the network')) is None


# ---------------------------------------------------------------------------------------------- structure
def test_provider_self_report_makes_acceptance_stricter_only():
    assert check('She change the meeting', 'She changed the meeting', ('grammar', 'She change', 'She changed'), meaning=False) == 'meaning_not_preserved'
    assert check('She change the meeting', 'She changed the meeting', ('grammar', 'She change', 'She changed'), adds=True) == 'information_added'


def test_every_edit_must_be_explained_by_a_grounded_note():
    source = 'I go store yesterday and buy milk'
    improved = 'I went to the store yesterday and bought milk'
    assert check(source, improved) == 'unexplained_changes'
    assert check(source, improved, ('grammar', 'go store', 'went to the store')) == 'unexplained_changes'  # 'buy' is unexplained
    assert check(source, improved, ('grammar', 'not in the source', 'went to the store')) == 'note_not_grounded'
    assert check(source, improved, ('grammar', 'go store', 'not in the improved text')) == 'note_not_grounded'
    assert check('The team fixed the bug', 'The team fixed the bug', ('grammar', 'fixed', 'repaired')) == 'inconsistent_notes'


def test_a_rewrite_is_rejected_for_length_or_volume_of_change():
    source = 'The team fixed the bug and tested it again today'
    assert check(source, 'The team fixed it', ('vocabulary', 'the bug and tested it again today', 'it')) in ('length_changed', 'content_removed')
    long = ' '.join(['The team fixed the bug and tested it again today'] * 3)
    assert check(source, long, ('grammar', 'The team', 'The team')) in ('length_changed', 'note_not_grounded')


def test_schema_rejects_more_than_two_notes_extras_and_empty_text():
    note = dict(kind='grammar', before='a', after='b', why='c')
    with pytest.raises(ValidationError):
        LanguageCoaching(improved_text='x', notes=[note, note, note], meaning_preserved=True, adds_information=False)
    with pytest.raises(ValidationError):
        LanguageCoaching(improved_text='x', notes=[], meaning_preserved=True, adds_information=False, extra='no')
    with pytest.raises(ValidationError):
        LanguageCoaching(improved_text='', notes=[], meaning_preserved=True, adds_information=False)
    with pytest.raises(ValidationError):
        Note(kind='opinion', before='a', after='b', why='c')
    with pytest.raises(ValidationError):
        Note(kind='grammar', before='a', after='b', why='c' * 300)


# ---------------------------------------------------------------------------------------------- eligibility
def test_eligibility_rules_for_reliable_confirmed_text():
    good = {'text': 'She change the meeting to Monday', 'confidence': .9, 'uncertainty': [], 'source': 'openai'}
    assert eligibility(good['text'], good, good['text'], .65) == (good['text'], None)
    assert eligibility(good['text'], {**good, 'uncertainty': ['unsure']}, good['text'], .65) == (None, 'unreliable_recognition')
    assert eligibility(good['text'], {**good, 'confidence': .5}, good['text'], .65) == (None, 'unreliable_recognition')
    assert eligibility(good['text'], {**good, 'confidence': None}, good['text'], .65)[1] == 'unreliable_recognition'
    assert eligibility(good['text'], None, good['text'], .65)[1] == 'unreliable_recognition'
    assert eligibility('too short', {**good, 'text': 'too short'}, 'too short', .65)[1] == 'too_short'
    assert eligibility('word ' * 400, {**good, 'text': 'word ' * 400}, 'word ' * 400, .65)[1] == 'too_long'
    assert eligibility('   ', good, '   ', .65) == (None, 'no_saved_answer')
    # A learner's own correction is confirmation, even when recognition was uncertain.
    assert reliable({**good, 'text': 'recognized differently', 'confidence': .1, 'uncertainty': ['x']}, good['text'], .65)
    assert reliable({'text': 'typed', 'source': 'demo_manual', 'uncertainty': ['Demo mode has no speech recognition.']}, 'typed text here', .65)
    assert available_state('no_saved_answer') is None and available_state('too_short').state == 'unavailable'
    assert available_state(None).state == 'available'


def test_source_hash_is_stable_and_trims_only_outer_whitespace():
    assert source_hash(' a b ') == source_hash('a b') != source_hash('a  b')


# ---------------------------------------------------------------------------------------------- diff
@pytest.mark.parametrize('source,improved', [
    ('She change meeting Monday', 'She changed the meeting to Monday.'),
    ('I go store yesterday and buy milk', 'I went to the store yesterday and bought milk'),
    ('same text', 'same text'),
    ('only deleted word here', 'only word here'),
    ('Add', 'Add one more word'),
    ('café “quotes” and don’t', 'café "quotes" and don’t'),
])
def test_diff_is_deterministic_and_reconstructs_both_texts(source, improved):
    first = diff(source, improved)
    assert first == diff(source, improved)
    assert ''.join(d.text for d in first if d.op != 'insert') == source
    assert ''.join(d.text for d in first if d.op != 'delete') == improved
    assert all(d.text for d in first)


def test_view_of_an_unchanged_answer_has_no_notes_or_diff_and_changed_words_are_case_insensitive():
    state = view('The team fixed it', 'The team fixed it', [Note(kind='grammar', before='a', after='b', why='c')])
    assert state.unchanged and state.notes == [] and state.diff == []
    assert changed_words('The Team fixed it', 'the team fixed it') == ([], [])
