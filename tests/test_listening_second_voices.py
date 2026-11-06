"""AUD-115: second voices for same-accent dialogue in the 12 accents that only have one. Nothing is enabled until the owner approves a voice (one catalog line); until
then same-accent dialogue stays unavailable and the learner is offered alternatives. Mocked synthesis only: no provider is called."""
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import accent_catalog as cat, accent_registry as registry
from app.config import Settings
from app.listening import generation as g
from app.listening.generation import Spec, assign_voices, format_availability, needs_choice, clamp_spec, spec_problem
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation import VOICES, lab, run, settings as gen_settings, start, view  # noqa: F401  (fixtures/helpers)
from test_listening_formats import lesson_rows, library, spec  # noqa: F401

ORIGINAL = ('american', 'british', 'australian')
TWELVE = tuple(a for a in cat.format_eligible_accents() if a not in ORIGINAL)


def speaker_one(accent):
    return registry.SELECTED_VOICES[accent].voice_id if accent in registry.SELECTED_VOICES else {v.accent: v.voice_id for v in registry.EXISTING_VOICES}[accent]


def test_every_one_of_the_twelve_accents_has_candidates_with_the_owners_voice_as_speaker_one():
    assert set(cat.LISTENING_CANDIDATES) == set(TWELVE) and len(TWELVE) == 12
    for accent, row in cat.LISTENING_CANDIDATES.items():
        assert row['first'] == speaker_one(accent), accent                                 # the owner's Audli 1.x selection, never replaced
        assert row['approved'] is None, accent                                              # and nothing is approved yet


def test_candidates_are_real_distinct_voices_never_reused_across_accents_or_as_a_speaker_one():
    every_first = {row['first'] for row in cat.LISTENING_CANDIDATES.values()} | {v[0] for v in cat.LISTENING_PAIRS.values() for v in [v['voices']]}
    seen = set()
    for accent, row in cat.LISTENING_CANDIDATES.items():
        ids = [v for v, _, _ in row['second']]
        assert ids and len(set(ids)) == len(ids) and all(re.fullmatch(r'[A-Za-z0-9]{20}', i) for i in ids), accent
        assert row['first'] not in ids, accent                                              # two genuinely different voices, never the same one twice
        assert not (set(ids) & every_first), accent                                         # and never another accent's speaker-1 voice
        assert not (set(ids) & seen), accent                                                # and no voice proposed for two accents
        seen |= set(ids)
        assert all(label.strip() for _, _, label in row['second'])


def test_until_the_owner_approves_a_voice_same_accent_dialogue_is_unavailable_and_alternatives_are_offered():
    assert cat.approved_second_voices() == {} and cat.approved_pairs() == {}
    assert format_availability(library())['conversation'] == list(ORIGINAL)
    for accent in TWELVE:
        problem = spec_problem(spec(accent=accent), library())
        assert problem == f'{cat.LABELS[accent]} is not available for this kind of lesson yet.', accent
        assert spec_problem(spec(format='story', accent=accent), library()) is None                                  # monologue works
        assert spec_problem(spec(accent='american', accent2=accent), library()) is None                              # mixed-accent works
        raw, notes = clamp_spec({'topic': 'a day out', 'cefr': 'B1', 'minutes': 2, 'format': 'dialogue', 'accent': cat.LABELS[accent]}, 2, library())
        choice = needs_choice(raw, notes, library())
        assert raw['accent'] == accent and choice and any(o['label'] == f'Make it a monologue in {cat.LABELS[accent]}' for o in choice['options']), accent      # asked, never swapped


@pytest.mark.parametrize('accent', TWELVE)
def test_once_approved_an_accent_gets_same_accent_dialogue_with_its_two_distinct_voices(lab, monkeypatch, accent):
    row = cat.LISTENING_CANDIDATES[accent]
    second = row['second'][0][0]
    monkeypatch.setitem(row, 'approved', second)
    assert cat.approved_pairs()[accent] == (speaker_one(accent), second)
    assert format_availability(library())['conversation'] == list(ORIGINAL) + [accent]          # appears with the existing flags, nobody else changes
    assert accent not in format_availability(gen_settings())['conversation']                    # flag off or accent not opened: still unavailable
    assert accent not in format_availability(gen_settings(listening_formats_enabled=True))['conversation']
    assert spec_problem(spec(accent=accent), library()) is None
    d = lab.deps(settings=library())
    d.voices = {**VOICES, **cat.approved_pairs()}
    request_id = start(lab, d, spec=spec(accent=accent), request=uuid4().hex[:16])
    assert run(lab, d) == 'done'
    version, audio, kinds, item = lesson_rows(lab, view(lab, lab.owner, request_id)['lesson_id'])
    first_id, second_id = (s['id'] for s in version['speakers'])
    expected = {first_id: speaker_one(accent), second_id: second}
    assert [(s['text'], expected[s['speaker']]) for s in version['sentences']] == list(d.speech.inner.calls)       # each sentence in its own speaker's voice, in order
    assert {v for _, v in d.speech.inner.calls} == {speaker_one(accent), second} and len({s['speaker'] for s in version['sentences']}) == 2
    assert [s['accent'] for s in version['speakers']] == [accent, accent] and item['accents'] == [accent]
    assert kinds == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']                                       # all four exercises
    cues = audio['alignment']
    assert len(cues) == len(version['sentences']) and all(a['start_ms'] < a['end_ms'] <= b['start_ms'] + 1 for a, b in zip(cues, cues[1:]))      # transcript sync


def test_approving_one_accent_changes_nothing_else_and_mixed_accent_stays_open_for_all_fifteen(monkeypatch):
    before = format_availability(library())
    monkeypatch.setitem(cat.LISTENING_CANDIDATES['irish'], 'approved', cat.LISTENING_CANDIDATES['irish']['second'][0][0])
    after = format_availability(library())
    assert after['monologue'] == before['monologue'] and after['mixed_accent_conversation'] == before['mixed_accent_conversation']
    assert len(after['mixed_accent_conversation']) == 15 and after['conversation'] == list(ORIGINAL) + ['irish']
    assert g.voices_from(library())['american'] == ('EXAVITQu4vr4xnSDxMaL', 'CwhRBWXzGAHq8TQ4Fs17')              # the originals are untouched
    two = [SimpleNamespace(id='a'), SimpleNamespace(id='b')]
    assert assign_voices(spec(accent='irish'), two, g.voices_from(library())) == {'a': 'DbwWo4rVEd5NrejHYUnm', 'b': cat.LISTENING_CANDIDATES['irish']['second'][0][0]}
    assert assign_voices(spec(accent='american', accent2='irish'), two, g.voices_from(library()))['b'] == 'DbwWo4rVEd5NrejHYUnm'      # mixed still uses the 1.x voice


def test_an_approval_that_is_not_a_listed_candidate_or_equals_speaker_one_is_refused(monkeypatch):
    row = cat.LISTENING_CANDIDATES['boston']
    for bad in ('x' * 20, row['first']):
        monkeypatch.setitem(row, 'approved', bad)
        with pytest.raises(ValueError):
            cat.approved_second_voices()
    monkeypatch.setitem(row, 'approved', row['second'][0][0])
    assert cat.approved_pairs()['boston'] == (row['first'], row['second'][0][0])


def test_a_configuration_override_cannot_give_an_unapproved_accent_a_pair():
    configured = '{"irish": ["' + 'A' * 20 + '", "' + 'B' * 20 + '"]}'
    assert 'irish' not in g.voices_from(Settings(listening_gen_voices=configured, _env_file=None))
