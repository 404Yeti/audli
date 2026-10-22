"""The AUD-77 evaluation harness is offline, deterministic and honest about what it measures (mock only)."""
import asyncio
import json
import socket
from pathlib import Path

import pytest

from app.improvement import LanguageCoaching
from scripts import evaluate_review_improvement as harness

REQUIRED = {'grammar', 'natural', 'vocabulary', 'comprehension', 'uncertain', 'negation', 'modality', 'quantity', 'comparison',
            'certainty', 'causality', 'entity', 'added_fact', 'removed_fact', 'synonym_swap', 'too_short', 'too_long', 'provider_error'}


@pytest.fixture(scope='module')
def cases():
    return harness.load_cases()


@pytest.fixture(scope='module')
def report(cases):
    return asyncio.run(harness.evaluate(cases))


def test_fixtures_cover_every_required_category_and_are_well_formed(cases):
    assert 50 <= len(cases) <= 70 and len({c['id'] for c in cases}) == len(cases)
    assert REQUIRED <= {c['category'] for c in cases}
    for case in cases:
        assert case['expect'] in ('accept', 'accept_unchanged', 'reject', 'unavailable', 'failed') and case['label']
        if case['candidate']:
            LanguageCoaching.model_validate(case['candidate'])           # candidates satisfy the provider contract
        if case['label'] in ('meaning_changing', 'comprehension_fixed'):
            assert case['expect'] == 'reject'
    assert all(c['passage_fact'] for c in cases if c['category'] == 'comprehension')


def test_no_candidate_that_changes_meaning_is_ever_accepted_and_ineligible_input_never_reaches_a_provider(report):
    s = report['scripted_verifier']
    assert s['meaning_changing_candidates']['accepted_FALSE_ACCEPTS'] == []
    assert s['comprehension_errors']['fixed_candidates_accepted'] == 0
    assert s['ineligible_inputs']['blocked_before_any_provider_call'] == s['ineligible_inputs']['total'] == 5
    assert s['provider_errors']['recorded_as_failed'] == s['provider_errors']['total'] == 2
    assert s['natural_answers']['acknowledged_unchanged'] == s['natural_answers']['total']
    assert s['privacy']['passage_facts_sent_to_provider'] == 0
    assert s['comprehension_errors']['preserved_and_accepted'] == s['comprehension_errors']['preserved_total']


def test_known_strictness_is_pinned_so_changes_in_either_direction_are_noticed(report):
    s = report['scripted_verifier']
    assert s['legitimate_corrections']['acceptance_rate'] >= .85
    assert {r['id'] for r in s['legitimate_corrections']['false_rejections']} <= {'v04', 'u06'}
    # What only the independent comparison can catch: removal, noun swaps and a fact fixed through vocabulary.
    assert report['caught_only_by_layer_4'] == ['c05', 'm22', 'm23', 'm24']
    assert set(report['lenient_verifier_worst_case']['meaning_changing_candidates']['accepted_FALSE_ACCEPTS']) == set(report['caught_only_by_layer_4'])


def test_results_are_deterministic_apart_from_timing(cases, report):
    again = asyncio.run(harness.evaluate(cases))
    strip = lambda rows: [{k: v for k, v in r.items() if k != 'local_ms'} for r in rows]  # noqa: E731
    assert strip(again['per_case']) == strip(report['per_case']) and again['fixtures_sha256'] == report['fixtures_sha256']


def test_cost_estimate_uses_the_documented_price_and_counts_only_calls_that_were_made(report):
    cost = report['scripted_verifier']['estimated_cost']
    assert (harness.PRICE_IN, harness.PRICE_OUT) == (.40, 1.60) and harness.MODEL == 'gpt-4.1-mini'
    assert 0 < cost['mean_usd_per_attempt'] < .002 and cost['p95_usd_per_attempt'] >= cost['mean_usd_per_attempt']
    assert cost['worst_case_usd_per_attempt_with_sdk_retries'] > cost['p95_usd_per_attempt']
    ineligible = [r for r in report['per_case'] if r['label'] == 'ineligible']
    assert all(r['usd'] == 0 and r['coach_calls'] == 0 for r in ineligible)
    unchanged = [r for r in report['per_case'] if r['outcome'] == 'accepted_unchanged']
    assert unchanged and all(r['check_calls'] == 0 for r in unchanged)   # nothing changed, nothing to compare


def test_harness_has_no_paid_mode_and_never_touches_the_network(monkeypatch, tmp_path, cases):
    def forbidden(*args, **kwargs):
        raise AssertionError('The evaluation harness must not use the network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    out = tmp_path / 'mock.json'
    assert harness.main(['mock', '--out', str(out)]) == 0 and json.loads(out.read_text())['harness'] == 'mock'
    planned = tmp_path / 'plan.json'
    assert harness.main(['plan', '--out', str(planned)]) == 0
    plan = json.loads(planned.read_text())
    assert plan['credentials_loaded'] is False and plan['network_calls'] == 0 and plan['executes_nothing'] is True
    assert plan['sdk_max_retries'] == 0 and plan['concurrency'] == 1 and plan['within_budget'] is True and 'PROPOSAL ONLY' in plan['status']
    with pytest.raises(SystemExit):
        harness.main(['live'])
    source = Path(harness.__file__).read_text()
    for forbidden_name in ('AsyncOpenAI', 'httpx', 'requests.', 'ELEVENLABS', 'OPENAI_API_KEY', 'os.environ', 'getenv'):
        assert forbidden_name not in source, forbidden_name
