"""AUD-108/109: one shared accent catalog for Audli 1.x and 2.0. Nothing about 1.x changes, nothing unvalidated becomes selectable in 2.0."""
import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from pending_voices import pending_unless_owner_approved  # noqa: F401  (autouse fixture)

from app import accent_catalog as cat
from app import accent_registry as registry
from app.listening import generation as g
from app.listening.generation import FakeInterpreter, Spec, clamp_spec, resolve_accent, spec_problem, voices_from
from app.config import Settings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
# sha256 of the canonical dump of the 1.x registry (vocabulary, labels, aliases, every voice record) taken from app/accent_registry.py BEFORE it was
# rebuilt on the shared catalog. If this changes, Audli 1.x's accent behaviour changed.
REGISTRY_1X_SNAPSHOT = '8481ff6c886794bf311b848436c1652dbd5a2da1e5390770420a1c6c4f9d8004'


def registry_dump():
    return json.dumps({'accent': list(registry.Accent.__args__), 'labels': registry.LABELS, 'aliases': registry.ALIASES,
                       'existing': [v.model_dump() for v in registry.EXISTING_VOICES], 'selected': {k: v.model_dump() for k, v in registry.SELECTED_VOICES.items()},
                       'names': registry.SELECTED_NAMES}, sort_keys=True)


# ---- backward compatibility with Audli 1.x --------------------------------------------------------------------------------------------------

def test_the_1x_registry_is_byte_for_byte_what_it_was_before_it_moved_onto_the_shared_catalog():
    assert hashlib.sha256(registry_dump().encode()).hexdigest() == REGISTRY_1X_SNAPSHOT


def test_1x_routing_rules_are_unchanged_for_every_approved_voice():
    for accent, record in registry.SELECTED_VOICES.items():
        assert registry.routing_allowed(accent, record.voice_id) and not registry.routing_allowed(accent, 'x' * 20)
    assert registry.routing_allowed('british', 'anything')                    # accents without an expanded selection are not gated by it (as before)


def test_neither_version_imports_the_other_through_the_catalog():
    tree = ast.parse((ROOT / 'app/accent_catalog.py').read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], 'the shared catalog must import nothing'
    for path in (ROOT / 'app/listening').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('app.accent'):
                assert node.module == 'app.accent_catalog', f'{path.name} reaches 1.x through {node.module}'
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith('app.accent_registry') for a in node.names)


# ---- the catalog's own integrity -----------------------------------------------------------------------------------------------------------

def test_every_accent_has_a_label_and_aliases_and_the_ids_are_unique():
    assert len(set(cat.ACCENT_IDS)) == len(cat.ACCENT_IDS) == 17
    assert set(cat.LABELS) == set(cat.ALIASES) == set(cat.ACCENT_IDS)
    for pattern in cat.ALIASES.values():
        re.compile(pattern)


def test_only_accents_with_an_enabled_pair_of_two_different_voices_can_be_generated():
    assert cat.enabled_listening_accents() == ('american', 'british', 'australian')            # the current production set, unchanged
    for accent, pair in cat.LISTENING_PAIRS.items():
        first, second = pair['voices']
        assert first != second and re.fullmatch(r'[A-Za-z0-9]{20}', first) and re.fullmatch(r'[A-Za-z0-9]{20}', second)
        assert accent in cat.ACCENT_IDS and pair['status'] in ('enabled', 'proposed')
    assert g.ACCENTS == cat.enabled_listening_accents()


def test_proposed_second_voices_are_only_a_shortlist_and_never_usable():
    approved_1x = {**{a: v for a, v, *_ in cat.SELECTED_PASSAGE_VOICES}, **{a: v for a, v, *_ in cat.EXISTING_PASSAGE_VOICES}}
    assert set(cat.LISTENING_CANDIDATES) == set(approved_1x) - {'american', 'british', 'australian'} and len(cat.LISTENING_CANDIDATES) == 12
    for accent, row in cat.LISTENING_CANDIDATES.items():
        assert accent not in cat.LISTENING_PAIRS and accent not in g.ACCENTS      # approval enables same-accent dialogue through approved_pairs(), never the original pair table
        assert row['first'] == approved_1x[accent]                                  # the first speaker is the voice 1.x already runs: both versions sound alike
        seconds = [v for v, _, _ in row['second']]
        assert row['first'] not in seconds and len(set(seconds)) == len(seconds)     # two distinguishable voices, never the same one twice
        assert all(re.fullmatch(r'[A-Za-z0-9]{20}', v) for v in seconds)
    assert cat.upcoming_listening_accents() == tuple(cat.LISTENING_CANDIDATES)


def test_accents_without_any_voice_have_no_path_at_all():
    for accent in ('welsh', 'south_african'):
        assert accent not in cat.LISTENING_PAIRS and accent not in cat.LISTENING_CANDIDATES and accent not in cat.single_voices()


# ---- interpretation --------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('said,expected', [('Irish', 'irish'), ('irish english', 'irish'), ('Indian English', 'indian'), ('new_zealand', 'new_zealand'), ('a Kiwi accent', 'new_zealand'),
                                           ('Southern American', 'southern'), ('American', 'american'), ('Australian English', 'australian'), ('UK', 'british'),
                                           ('Scottish', 'scottish'), ('Canadian', 'canadian'), ('South African', 'south_african'), ('Klingon', None), ('', None), (None, None), ('duke', None)])
def test_a_learners_words_resolve_to_one_catalog_accent_or_nothing(said, expected):
    assert resolve_accent(said) == expected


def test_enabled_accents_are_kept_whatever_the_spelling():
    for said, accent in (('British English', 'british'), ('australian', 'australian'), ('American', 'american')):
        spec, notes = clamp_spec({'topic': 'x y z', 'cefr': 'B1', 'accent': said, 'minutes': 2, 'format': 'dialogue'}, 2)
        assert spec['accent'] == accent and not [n for n in notes if 'accent' in n and not n.startswith('defaulted')]


@pytest.mark.parametrize('said,requested', [('Irish', 'irish'), ('Indian English', 'indian'), ('Scottish', 'scottish'), ('Canadian', 'canadian'), ('New Zealand', 'new_zealand'),
                                            ('Welsh', 'welsh'), ('South African', 'south_african')])
def test_a_named_accent_that_cannot_be_generated_is_never_swapped_the_learner_must_choose(said, requested):
    spec, notes = clamp_spec({'topic': 'x y z', 'cefr': 'B1', 'accent': said, 'minutes': 2, 'format': 'dialogue'}, 2)
    assert spec['accent'] == requested and 'accent_unavailable' in notes and f'requested_accent:{requested}' in notes     # what they asked for is kept, not replaced
    choice = g.needs_choice(spec, notes)
    assert choice and 'isn’t available' in choice['reason'] and choice['options']                                          # an explanation and alternatives, no silent choice
    assert {'patch': {'accent': 'british'}, 'label': 'Use British English instead'} in choice['options']
    with pytest.raises(ValidationError):
        Spec.model_validate({**spec, 'accent': 'klingon'})
    assert spec_problem(Spec.model_validate(spec)) and requested not in g.ACCENTS            # and the server will not generate it even if a client insists


def test_an_unrecognised_accent_word_asks_the_learner_instead_of_guessing():
    spec, notes = clamp_spec({'topic': 'x y z', 'cefr': 'B1', 'accent': 'Klingon', 'minutes': 2, 'format': 'dialogue'}, 2)
    assert spec['accent'] is None and 'accent_unrecognised' in notes
    assert g.needs_choice(spec, notes)['reason'] == 'We couldn’t tell which accent you meant.'


def test_nothing_is_asked_when_the_learner_said_nothing_or_asked_for_something_available():
    for raw in ({'accent': None}, {'accent': 'British English'}, {'accent': 'american'}):
        spec, notes = clamp_spec({'topic': 'x y z', 'cefr': 'B1', 'minutes': 2, 'format': 'dialogue', **raw}, 2)
        assert g.needs_choice(spec, notes) is None and spec['accent'] in ('american', 'british')                          # today's default behaviour is unchanged


def test_natural_requests_name_an_accent_only_when_the_learner_does_and_a_place_alone_is_not_one():
    ask = FakeInterpreter().interpret
    assert ask('Create a B2 listening lesson about ordering food in Dublin using Irish English.', 2)['accent'] == 'Irish English'
    assert ask('Create a C1 listening lesson about a business meeting using Indian English.', 2)['accent'] == 'Indian English'
    assert ask('Create a B1 listening lesson using an Irish accent.', 2)['accent'] == 'Irish English'
    assert ask('Create an A2 listening lesson about visiting a doctor in Canada.', 2)['accent'] is None     # the setting is not the accent
    assert ask('a lesson about coffee in New York, with American accents', 2)['accent'] == 'American English'  # a city in the topic does not become New York English
    assert 'ONLY if they name one' in g.INTERPRET_SYSTEM and 'NOT an accent' in g.INTERPRET_SYSTEM


# ---- voice selection -----------------------------------------------------------------------------------------------------------------------

def test_every_generatable_accent_speaks_with_two_different_voices():
    voices = voices_from(Settings(_env_file=None))
    assert set(voices) == set(g.ACCENTS)
    for accent, pair in voices.items():
        assert len(pair) == 2 and pair[0] != pair[1] and tuple(pair) == tuple(cat.LISTENING_PAIRS[accent]['voices'])


def test_configuration_cannot_enable_an_accent_the_catalog_has_not_approved():
    configured = json.dumps({'irish': ['DbwWo4rVEd5NrejHYUnm', 'BOt7zZh6gzfWlIUYnyPz'], 'british': ['A' * 20, 'B' * 20]})
    voices = voices_from(Settings(listening_gen_voices=configured, _env_file=None))
    assert 'irish' not in voices and voices['british'] == ('A' * 20, 'B' * 20)       # an override may swap voices of an enabled accent, never enable a new one


# ---- what the API tells the interface -------------------------------------------------------------------------------------------------------

from test_listening import make_repository  # noqa: E402,F401  (fixture)
from test_listening_generation_api import api, create  # noqa: E402,F401  (fixtures)


def test_the_availability_endpoint_offers_only_generatable_accents_and_names_the_coming_ones(api):
    body = api.a.get('/api/listening/generation/availability').json()
    assert body['accents'] == ['american', 'british', 'australian']
    assert body['coming_accents'] == [a for a in cat.ACCENT_IDS if a not in ('american', 'british', 'australian')]      # flag off: everything else is still coming
    assert body['accent_labels']['irish'] == 'Irish English' and body['accent_labels']['american'] == 'American English'
    assert not set(body['accents']) & set(body['coming_accents'])


def test_a_request_for_an_accent_that_cannot_be_generated_is_refused_by_the_server_itself(api):
    spec = {'topic': 'ordering food in Dublin', 'cefr': 'B2', 'accent': 'irish', 'minutes': 2, 'format': 'dialogue', 'scenario': ''}
    assert create(api.a, spec).status_code == 422                                    # a client cannot select it, whatever the interface shows
    assert api.a.post('/api/listening/generation/interpret', json={'client_request_id': 'r' * 16, 'text': 'A B1 lesson about tea using an Irish accent'}).status_code == 200
    api.work()
    done = [r for r in api.a.get('/api/listening/lessons').json()]
    assert all('irish' not in item['accents'] for item in done)
