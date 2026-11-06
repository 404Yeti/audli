"""AUD-112: single-speaker monologues, same-accent conversations and mixed-accent conversations. Mocked synthesis only: no provider is called, nothing is spent.

Every combination is gated by what the shared catalog actually has: a monologue needs one approved voice, a same-accent conversation two distinct voices,
a mixed-accent conversation one approved voice per accent. Nothing is substituted: an unavailable combination is refused or named, never swapped silently.
"""
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings
from app import accent_catalog as cat
from app.listening import generation as g, generation_jobs as gj, schema as t, store
from app.listening.generation import FakeScriptWriter, GenerationRefused, Spec, assign_voices, clamp_spec, format_availability, spec_problem
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation import SPEC, VOICES, lab, limits, run, settings as gen_settings, start, view  # noqa: F401  (fixtures/helpers)

IRISH, INDIAN, NEW_YORK = 'DbwWo4rVEd5NrejHYUnm', 'fydPJq00SigRIaPxWgiS', 'QzTKubutNn9TjrB7Xb2Q'      # the Audli 1.x voices the owner curated


def on(accents='irish,indian,new_york,welsh,scottish'):
    return gen_settings(listening_formats_enabled=True, listening_format_accents=accents)


def spec(**over):
    return Spec.model_validate({'topic': 'a day in Dublin', 'cefr': 'B1', 'accent': 'american', 'minutes': 2, 'format': 'dialogue', **over})


# ---- the three formats and what each can use --------------------------------------------------------------------------------------------------

def test_with_the_flag_off_the_available_set_is_exactly_what_production_offers_today():
    today = format_availability(Settings(_env_file=None))
    assert set(today['monologue']) == set(today['conversation']) == {'american', 'british', 'australian'} and today['mixed_accent_conversation'] == []


def test_the_flag_opens_only_the_accents_the_owner_lists_and_only_those_with_an_approved_voice():
    available = format_availability(on())
    assert set(available['monologue']) == {'american', 'british', 'australian', 'irish', 'indian', 'new_york', 'scottish'}      # welsh has no approved voice: never offered
    assert available['mixed_accent_conversation'] == available['monologue']
    assert available['conversation'] == ['american', 'british', 'australian']                                                    # same-accent pairs still need two voices
    assert 'canadian' not in available['monologue']                                                                              # approved voice exists, but the owner has not opened it
    assert format_availability(gen_settings(listening_formats_enabled=True))['monologue'] == format_availability(Settings(_env_file=None))['monologue']


@pytest.mark.parametrize('kwargs,flag_on,problem', [
    ({}, False, None),
    ({'format': 'story', 'accent': 'british'}, False, None),
    ({'format': 'story', 'accent': 'irish'}, False, 'Irish English is not available for this kind of lesson yet.'),
    ({'format': 'story', 'accent': 'irish'}, True, None),
    ({'format': 'story', 'accent': 'welsh'}, True, 'Welsh English is not available for this kind of lesson yet.'),
    ({'accent': 'irish'}, True, 'Irish English is not available for this kind of lesson yet.'),            # two Irish speakers need two Irish voices: not approved
    ({'accent2': 'irish'}, False, 'Mixed-accent conversations are not available yet.'),
    ({'accent2': 'irish'}, True, None),
    ({'accent': 'new_york', 'accent2': 'indian'}, True, None),
    ({'accent': 'american', 'accent2': 'canadian'}, True, 'Canadian English is not available for this kind of lesson yet.'),
])
def test_a_combination_is_generatable_only_if_every_voice_it_needs_exists_and_is_open(kwargs, flag_on, problem):
    assert spec_problem(spec(**kwargs), on() if flag_on else Settings(_env_file=None)) == problem


def test_the_spec_itself_rejects_nonsense_combinations():
    for bad in ({'accent': 'klingon'}, {'accent2': 'klingon'}, {'format': 'story', 'accent2': 'irish'}, {'accent2': 'american'}):
        with pytest.raises(ValidationError):
            spec(**bad)
    assert g.listening_kind(spec(format='story')) == 'monologue' and g.listening_kind(spec()) == 'conversation' and g.listening_kind(spec(accent2='irish')) == 'mixed_accent_conversation'


def test_voices_are_assigned_per_speaker_and_never_shared_or_substituted():
    two = [SimpleNamespace(id='a'), SimpleNamespace(id='b')]
    assert assign_voices(spec(), two, VOICES) == {'a': 'v-a1', 'b': 'v-a2'}                                   # same accent: its two distinct voices
    assert assign_voices(spec(format='story', accent='irish'), two[:1], VOICES) == {'a': IRISH}               # monologue: the owner's 1.x voice
    assert assign_voices(spec(accent2='irish'), two, VOICES) == {'a': 'v-a1', 'b': IRISH}                     # mixed: one voice per accent, speaker order = accent order
    assert assign_voices(spec(accent='new_york', accent2='indian'), two, VOICES) == {'a': NEW_YORK, 'b': INDIAN}
    with pytest.raises(GenerationRefused):                                                                     # an accent with one voice cannot be a same-accent conversation
        assign_voices(spec(accent='irish'), two, VOICES)
    with pytest.raises(GenerationRefused):                                                                     # an accent with no approved voice cannot speak at all
        assign_voices(spec(format='story', accent='welsh'), two[:1], VOICES)
    with pytest.raises(GenerationRefused):                                                                     # the same voice twice is never allowed
        assign_voices(spec(accent2='british'), two, {**VOICES, 'british': ('v-a1', 'v-b2')})


def test_the_natural_language_path_never_substitutes_it_keeps_the_request_and_offers_alternatives():
    base = {'topic': 'a day in Dublin', 'cefr': 'B1', 'minutes': 2}
    spec_raw, notes = clamp_spec({**base, 'format': 'dialogue', 'accent': 'british', 'accent2': 'indian'}, 2, Settings(_env_file=None))
    assert (spec_raw['accent'], spec_raw['accent2']) == ('british', 'indian') and 'mixed_unavailable' in notes and 'requested_accent2:indian' in notes     # kept, flag off
    choice = g.needs_choice(spec_raw, notes, Settings(_env_file=None))
    assert choice['reason'] == 'Mixed-accent conversations aren’t available yet.'
    assert [o['label'] for o in choice['options']] == ['Keep two speakers, both British English', 'Make it a monologue in British English']
    spec_raw, notes = clamp_spec({**base, 'format': 'dialogue', 'accent': 'british', 'accent2': 'indian'}, 2, on())
    assert spec_raw['accent2'] == 'indian' and g.needs_choice(spec_raw, notes, on()) is None                       # open: generatable as asked, nothing to confirm
    spec_raw, notes = clamp_spec({**base, 'format': 'story', 'accent': 'Irish English', 'accent2': 'indian'}, 2, on())
    assert spec_raw['accent'] == 'irish' and spec_raw['accent2'] is None and 'second_accent_ignored' in notes     # a monologue has one speaker (said)
    spec_raw, notes = clamp_spec({**base, 'format': 'story', 'accent': 'Welsh'}, 2, on())
    assert spec_raw['accent'] == 'welsh' and 'requested_accent:welsh' in notes and g.needs_choice(spec_raw, notes, on())['options']
    spec_raw, notes = clamp_spec({**base, 'format': 'dialogue', 'accent': 'american', 'accent2': 'american'}, 2, on())
    assert spec_raw['accent2'] is None and g.needs_choice(spec_raw, notes, on()) is None                          # same accent twice is an ordinary conversation
    spec_raw, notes = clamp_spec({**base, 'format': 'dialogue', 'accent': 'american', 'accent2': 'martian'}, 2, on())
    choice = g.needs_choice(spec_raw, notes, on())
    assert spec_raw['accent2'] is None and 'accent2_unrecognised' in notes and choice['options'][0]['patch'] == {'accent2': None}   # a second accent we could not read is asked about, never dropped silently


# ---- real jobs, mocked synthesis ----------------------------------------------------------------------------------------------------------

def generate(lab, **spec_kwargs):
    d = lab.deps(settings=on())
    s = spec(**spec_kwargs)
    request_id = start(lab, d, spec=s, request=uuid4().hex[:16])
    assert run(lab, d) == 'done'
    return d, request_id, view(lab, lab.owner, request_id)


def lesson_rows(lab, lesson_id):
    with lab.engine.connect() as db:
        version = db.execute(select(t.versions).where(t.versions.c.lesson_id == lesson_id)).mappings().one()
        audio = db.execute(select(t.audio).where(t.audio.c.version_id == version['id'])).mappings().one()
        kinds = [r.kind for r in db.execute(select(t.exercises.c.kind).where(t.exercises.c.version_id == version['id']).order_by(t.exercises.c.position))]
        catalog = {i['lesson_id']: i for i in store.list_catalog(db, lab.owner)}
    return version, audio, kinds, catalog[lesson_id]


def test_a_monologue_uses_one_voice_one_speaker_and_the_four_exercises(lab):
    d, request_id, done = generate(lab, format='story', accent='irish')
    version, audio, kinds, item = lesson_rows(lab, done['lesson_id'])
    assert len(version['speakers']) == 1 and version['speakers'][0]['accent'] == 'irish'
    assert {voice for _, voice in d.speech.inner.calls} == {IRISH}                                            # one approved voice, every sentence
    assert audio['accent'] == 'irish' and audio['voice'].endswith(f'={IRISH}') and item['accents'] == ['irish']
    assert kinds == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']
    assert {s['speaker'] for s in version['sentences']} == {version['speakers'][0]['id']}                      # no sentence belongs to a speaker who does not exist
    cues = audio['alignment']
    assert len(cues) == len(version['sentences']) and all(a['start_ms'] < a['end_ms'] <= b['start_ms'] + 1 for a, b in zip(cues, cues[1:]))      # transcript sync


def test_a_mixed_accent_conversation_voices_each_speaker_in_their_own_accent(lab):
    d, request_id, done = generate(lab, accent='american', accent2='irish')
    version, audio, kinds, item = lesson_rows(lab, done['lesson_id'])
    by_id = {s['id']: s for s in version['speakers']}
    assert [s['accent'] for s in version['speakers']] == ['american', 'irish']
    expected = {version['speakers'][0]['id']: 'v-a1', version['speakers'][1]['id']: IRISH}
    spoken = [(s['text'], expected[s['speaker']]) for s in version['sentences']]
    assert [(text, voice) for text, voice in d.speech.inner.calls] == spoken                                   # every sentence synthesised in ITS speaker's voice, in order
    assert {voice for _, voice in d.speech.inner.calls} == {'v-a1', IRISH} and len({s['speaker'] for s in version['sentences']}) == 2
    assert item['accents'] == ['american', 'irish']                                                           # the library tells the truth about both speakers
    assert kinds == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']
    assert len(audio['alignment']) == len(version['sentences'])


def test_a_same_accent_conversation_is_unchanged_and_records_both_speakers_accent(lab):
    d, request_id, done = generate(lab, accent='british')
    version, audio, kinds, item = lesson_rows(lab, done['lesson_id'])
    assert [s['accent'] for s in version['speakers']] == ['british', 'british'] and {v for _, v in d.speech.inner.calls} == {'v-b1', 'v-b2'}
    assert item['accents'] == ['british'] and audio['accent'] == 'british'


def test_a_request_is_refused_before_any_spend_if_the_flag_is_turned_off_after_it_was_accepted(lab):
    d_on = lab.deps(settings=on())
    request_id = start(lab, d_on, spec=spec(accent2='irish'), request='late')
    before = limits(lab)
    d_off = lab.deps(settings=gen_settings())                                                                  # flag off by the time the worker runs it
    assert run(lab, d_off) == 'failed'
    seen = view(lab, lab.owner, request_id)
    assert seen['phase'] == 'failed' and 'isn’t available' in seen['failure'] and seen['lesson_id'] is None
    assert d_off.speech.spent == 0 and d_off.speech.inner.calls == []                                          # nothing was synthesised
    after = limits(lab)
    assert all(after[k][0] <= before[k][0] and after[k][1] <= before[k][1] for k in after)                     # and the reservation was given back, not kept


def test_every_script_still_passes_the_same_quality_checks_for_each_format(lab):
    for kwargs in ({'format': 'story', 'accent': 'american'}, {}, {'accent2': 'irish'}):
        s = spec(**kwargs)
        raw = FakeScriptWriter().write(s, g.estimate_chars(2), None)
        content = g.assemble(s, raw, uuid4().hex, g.estimate_chars(2))
        assert len(content.speakers) == (1 if kwargs.get('format') == 'story' else 2)
        assert [sp.accent for sp in content.speakers] == g.speaker_accents(s)
        assert not re.search(r'\bspeakers?\b', content.exercise.prompt.lower())                              # no question about someone who is not there
    with pytest.raises(ValueError):                                                                           # a monologue with two speakers is rejected
        g.assemble(spec(format='story'), FakeScriptWriter().write(spec(), g.estimate_chars(2), None), uuid4().hex, g.estimate_chars(2))


# ---- through the real API: contracts, ownership, playing and finishing a lesson ------------------------------------------------------------

from test_listening_generation_api import api, create  # noqa: E402,F401  (fixtures)
from test_auth import AccountClient  # noqa: E402,F401


def api_with(api, **over):
    """The same app and database, with the flexible-formats flag set the way a test needs (the settings object is shared by routes and worker)."""
    for key, value in over.items():
        object.__setattr__(api.settings, key, value)
    return api


def test_the_api_refuses_unavailable_combinations_and_says_what_is_available(api):
    body = {'topic': 'a day in Dublin', 'cefr': 'B1', 'accent': 'american', 'minutes': 2, 'format': 'dialogue', 'scenario': ''}
    availability = api.a.get('/api/listening/generation/availability').json()
    assert availability['formats_enabled'] is False and availability['formats']['mixed_accent_conversation'] == []
    assert set(availability['formats']['monologue']) == {'american', 'british', 'australian'}
    assert create(api.a, {**body, 'format': 'story', 'accent': 'irish'}).status_code == 422                    # never swapped behind the learner's back
    assert create(api.a, {**body, 'accent2': 'irish'}).status_code == 422
    api_with(api, listening_formats_enabled=True, listening_format_accents='irish')
    availability = api.a.get('/api/listening/generation/availability').json()
    assert availability['formats_enabled'] is True and 'irish' in availability['formats']['monologue'] and 'irish' in availability['formats']['mixed_accent_conversation']
    assert availability['accent_labels']['irish'] == 'Irish English' and availability['formats']['conversation'] == ['american', 'british', 'australian']
    assert create(api.a, {**body, 'accent': 'irish'}).status_code == 422                                      # two Irish speakers still need two Irish voices
    assert create(api.a, {**body, 'format': 'story', 'accent': 'irish'}).status_code == 200


def test_a_mixed_accent_lesson_is_private_playable_and_finishes_with_the_normal_scoring(api):
    api_with(api, listening_formats_enabled=True, listening_format_accents='irish')
    made = create(api.a, {'topic': 'a hotel check-in', 'cefr': 'A2', 'accent': 'american', 'accent2': 'irish', 'minutes': 2, 'format': 'dialogue', 'scenario': ''}, request='m' * 16)
    assert made.status_code == 200
    api.work()
    final = api.a.get(f"/api/listening/generation/requests/{made.json()['id']}").json()
    assert final['phase'] == 'ready' and final['spec']['accent2'] == 'irish'
    lesson = final['lesson_id']
    assert api.b.post(f'/api/listening/lessons/{lesson}/attempts', json={'client_request_id': uuid4().hex}).status_code == 404         # still only theirs
    assert api.b.get(f"/api/listening/generation/requests/{made.json()['id']}").status_code == 404
    view = api.a.post(f'/api/listening/lessons/{lesson}/attempts', json={'client_request_id': uuid4().hex}).json()
    assert [s.get('accent') for s in view['lesson']['speakers']] == ['american', 'irish'] and 'correct_option_id' not in str(view)          # speaker attribution, no answer keys
    attempt = view['attempt']['id']
    by_kind = {e['kind']: e for e in view['exercises']}
    first = api.a.post(f'/api/listening/attempts/{attempt}/answers', json={'exercise_id': by_kind['multiple_choice']['id'], 'option_id': by_kind['multiple_choice']['options'][0]['id']})
    assert first.status_code == 200
    api.a.post(f'/api/listening/attempts/{attempt}/answers', json={'exercise_id': by_kind['fill_blank']['id'], 'text': 'x'})
    api.a.post(f'/api/listening/attempts/{attempt}/answers', json={'exercise_id': by_kind['sequencing']['id'], 'order': [o['id'] for o in by_kind['sequencing']['options']]})
    done = api.a.post(f"/api/listening/attempts/{attempt}/exercises/{by_kind['spoken']['id']}/skip").json()
    assert done['attempt']['status'] == 'completed' and done['attempt']['scored'] == 3                         # the spoken answer is never part of the score
    listed = {i['lesson_id']: i for i in api.a.get('/api/listening/lessons').json()}[lesson]
    assert listed['accents'] == ['american', 'irish'] and listed['status'] == 'completed'


def test_a_monologue_lesson_plays_with_one_speaker_and_the_existing_exercises(api):
    api_with(api, listening_formats_enabled=True, listening_format_accents='irish')
    made = create(api.a, {'topic': 'a Dublin tradition', 'cefr': 'B1', 'accent': 'irish', 'minutes': 2, 'format': 'story', 'scenario': ''}, request='s' * 16)
    api.work()
    lesson = api.a.get(f"/api/listening/generation/requests/{made.json()['id']}").json()['lesson_id']
    view = api.a.post(f'/api/listening/lessons/{lesson}/attempts', json={'client_request_id': uuid4().hex}).json()
    assert len(view['lesson']['speakers']) == 1 and view['lesson']['speakers'][0]['accent'] == 'irish'
    assert [e['kind'] for e in view['exercises']] == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']
    assert view['audio']['accent'] == 'irish' and api.a.get(f'/api/listening/attempts/{view["attempt"]["id"]}/audio').status_code == 200


# ---- the released library: every owner-curated Audli 1.x accent ----------------------------------------------------------------------------------

ALL = cat.format_eligible_accents()
ORIGINAL = ('american', 'british', 'australian')
PRODUCTION_ACCENTS = ','.join(a for a in ALL if a not in ORIGINAL)           # the value AUDLI_LISTENING_FORMAT_ACCENTS is set to in production


def library():
    return gen_settings(listening_formats_enabled=True, listening_format_accents=PRODUCTION_ACCENTS)


def test_the_released_library_is_exactly_the_approved_accents_and_nothing_else():
    assert ALL == ('british', 'american', 'australian', 'scottish', 'indian', 'irish', 'canadian', 'new_zealand', 'chinese', 'japanese', 'filipino', 'nigerian', 'southern', 'new_york', 'boston')
    available = format_availability(library())
    assert set(available['monologue']) == set(available['mixed_accent_conversation']) == set(ALL) and len(available['monologue']) == 15
    assert available['conversation'] == list(ORIGINAL)                                   # same accent twice needs two distinct approved voices
    assert g.coming_accents(library()) == ['welsh', 'south_african']                      # no approved voice: unavailable, never substituted
    for gone in ('welsh', 'south_african'):
        for kwargs in ({'format': 'story'}, {}, {'accent2': 'american'}):
            assert spec_problem(spec(accent=gone, **kwargs), library())
    assert 'welsh' not in PRODUCTION_ACCENTS and 'south_african' not in PRODUCTION_ACCENTS


def test_the_owners_original_voice_selections_are_the_voices_that_speak():
    from app import accent_registry as registry
    voices = g.voices_from(library())
    for accent in ALL:
        expected = voices[accent][0] if accent in voices else registry.SELECTED_VOICES[accent].voice_id if accent in registry.SELECTED_VOICES else {v.accent: v.voice_id for v in registry.EXISTING_VOICES}[accent]
        assert g.single_voice(accent, voices) == expected
    assert voices['american'] == ('EXAVITQu4vr4xnSDxMaL', 'CwhRBWXzGAHq8TQ4Fs17') and voices['british'] == ('Xb7hH8MSUJpSbSDYk0k2', 'onwK4e9ZLuTAKqWW03F9')   # untouched
    assert voices['australian'] == ('tyepWYJJwJM9TTFIg5U7', 'IKne3meq5aSn9XLyUdCD')
    for accent in ('southern', 'new_york', 'boston', 'chinese', 'japanese', 'filipino', 'nigerian'):               # character and first-language accents are in the library on purpose
        assert accent in ALL and spec_problem(spec(format='story', accent=accent), library()) is None


@pytest.mark.parametrize('accent', ALL)
def test_every_available_accent_speaks_a_monologue_in_its_own_approved_voice(lab, accent):
    d = lab.deps(settings=library())
    request_id = start(lab, d, spec=spec(format='story', accent=accent), request=uuid4().hex[:16])
    assert run(lab, d) == 'done'
    done = view(lab, lab.owner, request_id)
    version, audio, kinds, item = lesson_rows(lab, done['lesson_id'])
    voice = g.single_voice(accent, VOICES)
    assert len(version['speakers']) == 1 and version['speakers'][0]['accent'] == accent and item['accents'] == [accent]
    assert {v for _, v in d.speech.inner.calls} == {voice} and audio['accent'] == accent
    assert kinds == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']                                 # all four exercises work for a monologue
    cues = audio['alignment']
    assert len(cues) == len(version['sentences']) and all(a['start_ms'] < a['end_ms'] <= b['start_ms'] + 1 for a, b in zip(cues, cues[1:]))      # transcript sync


@pytest.mark.parametrize('accent', [a for a in ALL if a != 'american'])
def test_every_available_accent_can_be_one_speaker_of_a_mixed_accent_conversation(lab, accent):
    d = lab.deps(settings=library())
    request_id = start(lab, d, spec=spec(accent='american', accent2=accent), request=uuid4().hex[:16])
    assert run(lab, d) == 'done'
    version, audio, kinds, item = lesson_rows(lab, view(lab, lab.owner, request_id)['lesson_id'])
    first, second = (s['id'] for s in version['speakers'])
    expected = {first: g.single_voice('american', VOICES), second: g.single_voice(accent, VOICES)}
    assert [(s['text'], expected[s['speaker']]) for s in version['sentences']] == list(d.speech.inner.calls)      # each sentence in its own speaker's voice and accent
    assert [s['accent'] for s in version['speakers']] == ['american', accent] and item['accents'] == ['american', accent]
    assert kinds == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing'] and len(audio['alignment']) == len(version['sentences'])


def test_every_pair_of_different_available_accents_gets_two_distinct_voices_in_speaker_order():
    voices = g.voices_from(library())
    speakers = [SimpleNamespace(id='a'), SimpleNamespace(id='b')]
    seen = 0
    for first in ALL:
        for second in ALL:
            if first == second:
                continue
            assigned = assign_voices(spec(accent=first, accent2=second), speakers, voices)
            assert assigned['a'] == g.single_voice(first, voices) and assigned['b'] == g.single_voice(second, voices) and assigned['a'] != assigned['b']
            assert spec_problem(spec(accent=first, accent2=second), library()) is None
            seen += 1
    assert seen == 15 * 14


def test_same_accent_conversations_stay_limited_to_accents_with_two_distinct_voices():
    for accent in ALL:
        problem = spec_problem(spec(accent=accent), library())
        assert (problem is None) == (accent in ORIGINAL), accent


def test_the_public_health_endpoint_reports_which_accents_each_format_offers_with_no_voice_ids(api):
    health = api.a.get('/api/health').json()['listening_formats']
    assert health['enabled'] is False and set(health['monologue']) == set(ORIGINAL) and health['mixed_accent_conversation'] == []
    api_with(api, listening_formats_enabled=True, listening_format_accents=PRODUCTION_ACCENTS)
    health = api.a.get('/api/health').json()['listening_formats']
    assert health['enabled'] is True and len(health['monologue']) == 15 and len(health['mixed_accent_conversation']) == 15 and health['conversation'] == list(ORIGINAL)
    assert not any(len(str(v)) == 20 and str(v).isalnum() for value in health.values() if isinstance(value, list) for v in value)       # ids of accents only, never a voice id
