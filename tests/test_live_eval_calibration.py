"""The seeded calibration stage, the stage gate, the confidence bounds and the proposed release thresholds of the AUD-77 live runner.

Mock transport only. Nothing here contacts a provider, reads a credential or touches a database."""
import asyncio
import json

import pytest

from scripts import eval_statistics as stats
from scripts import run_review_improvement_eval as runner
from tests.test_live_eval_runner import NOW, Server, approval, run

PAIRS = {p['id']: p for p in runner.load_calibration()}
CAL_ONLY = {'stages': ['calibration'], 'max_requests': len(PAIRS)}
HELD_ONLY = {'stages': ['heldout']}
NOT_CAUGHT_BEFORE_THE_COMPARISON = {'c19', 'c20', 'c26', 'c27'}      # layers 1-3 cannot see these: the comparison step alone must


def lenient():
    """A careless comparison model that calls every pair the same."""
    return {(p['original'], p['improved']): {'same_meaning': True} for p in PAIRS.values()}


# ------------------------------------------------------------------ the calibration stage
def test_every_seeded_pair_is_asked_once_and_a_correct_comparison_model_leaks_nothing(tmp_path):
    server = Server()
    report, out = run(tmp_path, server, **CAL_ONLY)
    cal = report['calibration']['summary']
    checks = [r for r in server.requests if 'original' in r]
    assert len(server.requests) == len(checks) == len(PAIRS)                      # one request per pair; the pipeline never asks again
    assert cal['seeded_unsafe']['leaks'] == [] and cal['seeded_unsafe']['n'] == 27 and cal['seeded_same_meaning']['pipeline_accepted'] == 12
    assert cal['seeded_unsafe']['comparison_alone'] == {'caught': 27, 'missed': [], 'unknown': 0}
    assert set(cal['seeded_unsafe']['not_caught_before_the_comparison']) == NOT_CAUGHT_BEFORE_THE_COMPARISON
    assert report['records'] == [] and report['heldout_skipped'] is None and report['completed'] is True


def test_a_lenient_comparison_model_leaks_exactly_what_layers_one_to_three_cannot_see(tmp_path):
    report, _ = run(tmp_path, Server(check=lenient()), **CAL_ONLY)
    cal = report['calibration']['summary']['seeded_unsafe']
    assert set(cal['leaks']) == NOT_CAUGHT_BEFORE_THE_COMPARISON                  # the deterministic layers still reject the other 23
    assert cal['comparison_alone']['caught'] == 0 and len(cal['comparison_alone']['missed']) == 27
    assert report['calibration']['summary']['classifications']['FALSE_ACCEPT'] == 4


def test_a_leak_in_calibration_skips_the_held_out_stage_and_spends_nothing_on_it(tmp_path):
    server = Server(check=lenient())
    report, out = run(tmp_path, server)
    assert report['heldout_skipped'] and 'c19' in report['heldout_skipped'] and report['completed'] is False and report['records'] == []
    assert len(server.requests) == len(PAIRS) and all('original' in r for r in server.requests)      # not one coaching request
    assert 'Not run' in (out / 'report.md').read_text() and 'FALSE_ACCEPT' in json.dumps(report['calibration']['records'])
    assert report['release_criteria']['all_gates_pass'] is False and 'heldout_false_accepts' in report['release_criteria']['gates_not_evaluated']


@pytest.mark.parametrize('leaky', sorted(NOT_CAUGHT_BEFORE_THE_COMPARISON))
def test_a_single_accepted_unsafe_pair_stops_the_evaluation_before_any_held_out_request(tmp_path, leaky):
    pair = PAIRS[leaky]
    server = Server(check={(pair['original'], pair['improved']): {'same_meaning': True}})        # every other pair is judged correctly
    report, out = run(tmp_path, server)
    assert report['calibration']['summary']['seeded_unsafe']['leaks'] == [leaky] and leaky in report['heldout_skipped']
    assert all('original' in r for r in server.requests) and len(server.requests) == len(PAIRS)      # not one coaching request after the leak
    ledger = [json.loads(line) for line in (out / 'ledger.jsonl').read_text().splitlines()]
    assert {e['call'] for e in ledger if e['event'] == 'intent'} == {'check'} and report['completed'] is False


def test_the_label_review_changes_are_exactly_the_documented_ones():
    cases = {c['id']: c for c in runner.load_cases()}
    assert 'a01' not in cases and cases['f54']['source'] == 'This box is more heavier than that one'
    assert cases['a05']['source'] == 'Tomorrow I saw the doctor about my knee' and cases['a05']['category'] == 'ambiguous_intent'
    assert sorted(c for c in cases if cases[c]['category'] == 'ambiguous_intent') == ['a02', 'a03', 'a04', 'a05']


def test_calibration_always_runs_before_the_held_out_stage(tmp_path):
    server = Server()
    run(tmp_path, server)
    kinds = ['check' if 'original' in r else 'coach' for r in server.requests]
    assert kinds[:len(PAIRS)] == ['check'] * len(PAIRS) and 'coach' in kinds[len(PAIRS):]


def test_the_held_out_stage_alone_never_sends_a_seeded_pair(tmp_path):
    server = Server()
    report, _ = run(tmp_path, server, **HELD_ONLY)
    assert report['calibration'] is None and report['summary']['attempts_recorded'] == len(runner.load_cases())
    seeded = {(p['original'], p['improved']) for p in PAIRS.values()}
    assert not [r for r in server.requests if (r.get('original'), r.get('improved')) in seeded]
    assert report['release_criteria']['all_gates_pass'] is False and 'seeded_unsafe_leaks' in report['release_criteria']['gates_not_evaluated']


def test_a_failed_comparison_is_never_resent_and_a_pair_the_deterministic_layers_reject_is_still_rejected(tmp_path):
    victim = PAIRS['c01']          # rejected by layer 3 whatever the comparison says
    undecided = PAIRS['c19']       # needs the comparison to be rejected
    server = Server()
    original_handle = server.handle

    async def failing(request):
        user = json.loads(json.loads(request.content)['input'][1]['content'])
        if user.get('original') in (victim['original'], undecided['original']):
            import httpx
            return httpx.Response(500, json={'error': {'message': 'boom', 'type': 'server_error'}})
        return await original_handle(request)
    server.handle = failing
    report, out = run(tmp_path, server, **CAL_ONLY)
    records = {r['id']: r for r in report['calibration']['records']}
    assert records['c01']['classification'] == 'correctly_rejected' and records['c01']['comparison_alone'] is None and records['c01']['layer'] == '3'
    assert records['c19']['classification'] == 'provider_error' and records['c19']['comparison_error']
    ledger = [json.loads(line) for line in (out / 'ledger.jsonl').read_text().splitlines()]
    assert len([e for e in ledger if e['event'] == 'intent']) == len(PAIRS) and len([e for e in ledger if e['event'] == 'unknown']) == 2     # exactly one request each
    assert report['calibration']['summary']['seeded_unsafe']['comparison_alone']['unknown'] == 2


def test_the_budget_stopping_calibration_skips_the_held_out_stage(tmp_path):
    server = Server(usage=(1500, 700))
    report, out = run(tmp_path, server, max_usd=.003)
    assert report['completed'] is False and report['heldout_skipped'] == 'the budget stopped the calibration stage' and report['records'] == []
    assert report['calibration']['summary']['classifications'].get('not_run') == 1
    assert report['summary']['cost_usd']['charged_total'] <= .003 and report['cap_overrun'] is False


def test_the_pipeline_decision_for_a_pair_is_the_production_decision_path(monkeypatch):
    from app import improvement
    assert runner.coach is improvement.coach and runner.SeededProvider.__module__ == 'scripts.run_review_improvement_eval'
    # a pair the comparison says is different is rejected at layer 4 with the production code, and never accepted
    pair = PAIRS['c19']
    record = asyncio.run(_one_pair(pair, same=False))
    assert (record['layer'], record['code'], record['classification']) == ('4', 'meaning_check_failed', 'correctly_rejected')
    record = asyncio.run(_one_pair(pair, same=True))
    assert record['classification'] == 'FALSE_ACCEPT'


async def _one_pair(pair, same):
    class Guarded:
        calls = []

        def begin(self, case_id):
            pass

        async def check_meaning(self, original, improved):
            return runner.MeaningCheck(same_meaning=same)
    return await runner.run_pair(pair, Guarded())


# ------------------------------------------------------------------ overlap and isolation
def test_the_run_refuses_a_held_out_source_that_was_tuned_on_or_seeded(tmp_path, monkeypatch):
    tuned = runner.load_cases(runner.PRIOR_FIXTURES)[0]['source']
    cases = runner.load_cases()
    leaked = [{**cases[0], 'source': tuned}] + cases[1:]
    real_load = runner.load_cases
    monkeypatch.setattr(runner, 'load_cases', lambda path=runner.FIXTURES: leaked if path == runner.FIXTURES else real_load(path))
    with pytest.raises(runner.RefusedToRun, match='tuned on'):
        asyncio.run(runner.run_live(approval(), tmp_path / 'run', env={}, http_client=Server().client(), now=NOW))
    assert not (tmp_path / 'run').exists()
    monkeypatch.setattr(runner, 'load_cases', real_load)
    seeded = runner.load_calibration()
    monkeypatch.setattr(runner, 'load_calibration', lambda path=runner.CALIBRATION: [{**seeded[0], 'original': cases[0]['source']}] + seeded[1:])
    with pytest.raises(runner.RefusedToRun, match='calibration pairs overlap'):
        asyncio.run(runner.run_live(approval(), tmp_path / 'run2', env={}, http_client=Server().client(), now=NOW))


def test_the_extension_adds_no_network_storage_or_environment_access():
    import ast
    from pathlib import Path
    for module in (runner.__file__, stats.__file__):
        tree = ast.parse(Path(module).read_text())
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in imported if m.startswith(('sqlalchemy', 'psycopg', 'app.storage', 'app.main', 'requests', 'httpx', 'urllib', 'socket'))], (module, imported)
    assert 'os.environ[' not in Path(runner.__file__).read_text() and 'getenv' not in Path(stats.__file__).read_text()


# ------------------------------------------------------------------ the approval's stages
def test_a_calibration_only_approval_needs_only_the_calibration_requests_and_a_held_out_only_one_needs_the_held_out_ones():
    plan = runner.plan(repeats=1)
    assert runner.validate_approval(approval(**CAL_ONLY), plan, NOW) == []
    held = plan['stages']['heldout']['max_requests']
    assert runner.validate_approval(approval(stages=['heldout'], max_requests=held), plan, NOW) == []
    assert any('max_requests' in p for p in runner.validate_approval(approval(stages=['heldout'], max_requests=held - 1), plan, NOW))
    assert any('max_requests' in p for p in runner.validate_approval(approval(stages=['calibration'], max_requests=len(PAIRS) - 1), plan, NOW))
    assert any('max_requests' in p for p in runner.validate_approval(approval(max_requests=plan['max_requests'] - 1), plan, NOW))
    assert runner.validate_approval(approval(stages=['heldout', 'calibration']), plan, NOW) == []       # order in the file does not matter; the run order is fixed


def test_validate_reports_the_stages_and_pairs(tmp_path):
    path = tmp_path / 'a.json'
    path.write_text(json.dumps(approval(expires_at='2099-01-01T00:00:00Z')))
    code, message = runner.validate_command(path, env={'PATH': '/usr/bin'}, now=NOW)
    assert code == 0 and "stages ['calibration', 'heldout']" in message and '39 seeded pairs' in message and '100 cases' in message


# ------------------------------------------------------------------ statistics
def test_the_confidence_bounds_match_known_values():
    assert stats.clopper_pearson_upper(0, 24) == pytest.approx(0.1170, abs=.002) and stats.clopper_pearson_upper(0, 27) == pytest.approx(0.105, abs=.002)
    assert stats.clopper_pearson_upper(6, 64) == pytest.approx(0.177, abs=.003) and stats.clopper_pearson_upper(7, 64) == pytest.approx(0.196, abs=.003)
    assert stats.clopper_pearson_upper(3, 32) == pytest.approx(0.225, abs=.003) and stats.clopper_pearson_upper(64, 64) == 1.0 and stats.clopper_pearson_upper(0, 0) == 1.0
    assert stats.wilson_upper(6, 64) == pytest.approx(0.190, abs=.003) and 0 < stats.wilson_upper(0, 30) < .12
    assert stats.trials_needed_for_zero_failures(.05) == 59 and stats.trials_needed_for_zero_failures(.10) == 29
    assert stats.pass_probability(6, 64, .05) == pytest.approx(.96, abs=.01) and stats.pass_probability(6, 64, .15) < .2
    assert stats.rate(2, 40) == {'failures': 2, 'n': 40, 'point': .05, 'upper95_exact': stats.rate(2, 40)['upper95_exact'], 'upper95_wilson': stats.rate(2, 40)['upper95_wilson']}


# ------------------------------------------------------------------ the ratified provisional release gates
def record(id_, expect, classification, good=True, f1=1.0, category='x', calls=1):
    return {'id': id_, 'expect': expect, 'category': category, 'classification': classification, 'calls': [{'ok': True, 'latency_ms': 1000, 'usd': .0004}] * calls,
            'score': {'quality_good': good, 'invariants_ok': True, 'best_f1': f1}}


def empty_summary(**over):
    base = {'meaning_preservation': {'FALSE_ACCEPTS': []}, 'provider': {'requests': 100, 'failed_or_unknown': 0},
            'latency_ms': {'per_improvement': {'p95': 3000}}, 'cost_usd': {'mean_per_attempt': .0005}}
    return {**base, **over}


def calibration(leaks=()):
    return {'seeded_unsafe': {'n': 27, 'leaks': list(leaks), 'leak_rate': stats.rate(len(leaks), 27)}, 'seeded_same_meaning': {'n': 12},
            'provider': {'requests': 39, 'failed_or_unknown': 0}}


def passing_records(false_rejections=6, edits=3):
    records = [record(f'f{i}', 'accept', 'false_rejection' if i < false_rejections else 'accepted_ok') for i in range(64)]
    records += [record(f'n{i}', 'accept_unchanged', 'unneeded_edit_accepted' if i < edits else 'unchanged_ok') for i in range(32)]
    return records


def verdicts(criteria):
    return {c['name']: c['pass'] for c in criteria['criteria']}


def test_the_ratified_gates_are_exactly_the_owners_and_match_the_dataset_sizes():
    gates = runner.RELEASE_GATES
    assert gates['heldout_false_accepts']['max'] == 0 and gates['seeded_unsafe_leaks']['max'] == 0
    assert gates['false_rejections'] == {'max': 6, 'of': 64, 'gate': True, 'denominator': 'fix cases sent to the provider'}
    assert gates['unnecessary_edits']['max'] == 3 and gates['unnecessary_edits']['of'] == 32
    assert gates['correction_yield']['min'] == .80 and gates['mean_best_f1_of_accepted']['min'] == .95
    assert gates['provider_failure_rate']['max'] == .02 and gates['latency_p95_per_improvement_ms']['max'] == 5000
    assert not gates['mean_cost_per_attempt_usd'].get('gate')                 # cost is controlled by the $1.00 hard cap, not a ratified gate
    cases = runner.load_cases()
    assert sum(c['expect'] == 'accept' for c in cases) == runner.GATE_FIX_CASES == 64
    assert sum(c['expect'] == 'accept_unchanged' and c['category'] != 'ambiguous_intent' for c in cases) == runner.GATE_NATURAL_ANSWERS == 32


def test_the_gates_pass_at_their_exact_boundary_and_fail_one_step_past_it():
    ok = runner.release_criteria(empty_summary(), passing_records(6, 3), calibration())
    assert verdicts(ok)['false_rejections'] is True and verdicts(ok)['unnecessary_edits'] is True
    fr = next(c for c in ok['criteria'] if c['name'] == 'false_rejections')['value']
    assert (fr['failures'], fr['n']) == (6, 64) and fr['upper95_exact'] == pytest.approx(.177, abs=.003)      # reported, not gating
    ue = next(c for c in ok['criteria'] if c['name'] == 'unnecessary_edits')['value']
    assert (ue['failures'], ue['n']) == (3, 32) and ue['upper95_exact'] == pytest.approx(.225, abs=.003)
    assert verdicts(runner.release_criteria(empty_summary(), passing_records(7, 3), calibration()))['false_rejections'] is False
    assert verdicts(runner.release_criteria(empty_summary(), passing_records(6, 4), calibration()))['unnecessary_edits'] is False
    assert runner.release_criteria(empty_summary(), passing_records(7, 3), calibration())['all_gates_pass'] is False


def test_one_unsafe_acceptance_anywhere_fails_the_gate():
    assert verdicts(runner.release_criteria(empty_summary(meaning_preservation={'FALSE_ACCEPTS': ['f1']}), passing_records(0, 0), calibration()))['heldout_false_accepts'] is False
    assert verdicts(runner.release_criteria(empty_summary(), passing_records(0, 0), calibration(['c19'])))['seeded_unsafe_leaks'] is False
    assert runner.release_criteria(empty_summary(), passing_records(0, 0), calibration())['all_gates_pass'] is True


@pytest.mark.parametrize('summary,name', [
    (empty_summary(provider={'requests': 100, 'failed_or_unknown': 3}), 'provider_failure_rate'),
    (empty_summary(latency_ms={'per_improvement': {'p95': 5001}}), 'latency_p95_per_improvement_ms'),
])
def test_reliability_and_latency_gates(summary, name):
    result = runner.release_criteria(summary, passing_records(0, 0), calibration())
    assert verdicts(result)[name] is False and result['all_gates_pass'] is False


def test_cost_is_reported_but_never_fails_the_gates():
    result = runner.release_criteria(empty_summary(cost_usd={'mean_per_attempt': .01}), passing_records(0, 0), calibration())
    assert verdicts(result)['mean_cost_per_attempt_usd'] is None and result['all_gates_pass'] is True


def test_yield_quality_and_the_ambiguous_category_are_handled_separately():
    records = [record(f'f{i}', 'accept', 'accepted_ok' if i < 50 else 'rejected_poor_candidate', good=i < 50) for i in range(64)]       # 78% yield
    assert verdicts(runner.release_criteria(empty_summary(), records, calibration()))['correction_yield'] is False
    low_f1 = [record(f'f{i}', 'accept', 'accepted_ok', f1=.9) for i in range(64)]
    assert verdicts(runner.release_criteria(empty_summary(), low_f1, calibration()))['mean_best_f1_of_accepted'] is False
    ambiguous = passing_records(0, 0) + [record('a1', 'accept_unchanged', 'unneeded_edit_accepted', category='ambiguous_intent')]
    result = runner.release_criteria(empty_summary(), ambiguous, calibration())
    assert verdicts(result)['unnecessary_edits'] is True and result['informational']['ambiguous_intent_edits_accepted'] == ['a1']      # reported, not gated


def test_a_gate_that_could_not_be_evaluated_is_never_a_pass():
    result = runner.release_criteria(empty_summary(), [], None)
    assert result['all_gates_pass'] is False and result['gates_not_evaluated'] and 'not proof of universal safety' in result['status']


def test_a_complete_mock_run_reports_the_criteria_with_bounds_and_renders_them(tmp_path):
    report, out = run(tmp_path, Server())
    rc = report['release_criteria']
    text = (out / 'report.md').read_text()
    assert report['completed'] is True and rc['status'].startswith('Release gates RATIFIED') and not rc['gates_not_evaluated']
    assert 'Seeded calibration' in text and 'Release gates' in text and 'seeded_unsafe_leaks' in text
    assert verdicts(rc)['seeded_unsafe_leaks'] is True and verdicts(rc)['heldout_false_accepts'] is True
