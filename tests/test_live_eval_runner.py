"""The AUD-77 live evaluation runner: plan, approval gate, spending cap, no retries, durable ledger and scoring.

Every test uses a mock HTTP transport. No test, and no part of the runner without an approval file, contacts a provider.
"""
import ast
import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from scripts import run_review_improvement_eval as runner

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
MODEL = runner.PINNED_MODEL


def approval(**overrides):
    plan = runner.plan(repeats=1)
    base = {'approved': True, 'approved_by': 'owner', 'approved_at': '2026-10-22T10:00:00Z', 'expires_at': '2026-10-23T10:00:00Z', 'run_id': 'run-1',
            'model': MODEL, 'prompt_version': runner.PROMPT_VERSION, 'fixtures_sha256': plan['fixtures_sha256'],
            'prices_usd_per_million': {'input': runner.PRICE_IN, 'output': runner.PRICE_OUT}, 'max_usd': 1.0,
            'max_requests': plan['max_requests'], 'repeats': 1}
    return {**base, **overrides}


class Server:
    """A mock Responses API. By default the 'model' returns the learner's text unchanged and the comparison agrees."""
    def __init__(self, usage=(1500, 120), model=MODEL, coach=None, check=None, fail_first_for=None, ledger_dir=None, delay=0):
        self.usage, self.model, self.coach_for, self.check_for = usage, model, coach or {}, check or {}
        self.requests, self.in_flight, self.max_in_flight, self.fail_first_for, self.ledger_dir, self.delay = [], 0, 0, fail_first_for, ledger_dir, delay
        self.failed = set()
        self.ledger_snapshots = []

    def envelope(self, payload):
        tokens_in, tokens_out = self.usage
        return httpx.Response(200, json={'id': 'resp', 'object': 'response', 'created_at': 0, 'status': 'completed', 'model': self.model,
            'output': [{'type': 'message', 'id': 'm', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': json.dumps(payload), 'annotations': []}]}],
            'parallel_tool_calls': False, 'tool_choice': 'auto', 'tools': [],
            'usage': {'input_tokens': tokens_in, 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens': tokens_out,
                      'output_tokens_details': {'reasoning_tokens': 0}, 'total_tokens': tokens_in + tokens_out}})

    async def handle(self, request):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            body = json.loads(request.content)
            user = json.loads(body['input'][1]['content'])
            assert body['store'] is False and body.get('max_output_tokens')                  # bounded output, nothing stored
            if self.ledger_dir is not None:                                                    # intent must already be on disk
                lines = [json.loads(line) for line in (self.ledger_dir / 'ledger.jsonl').read_text().splitlines()]
                assert lines[-1]['event'] == 'intent', lines[-1]
                self.ledger_snapshots.append(lines[-1]['seq'])
            self.requests.append(user)
            if 'learner_text' in user:
                text = user['learner_text']
                if self.fail_first_for and text == self.fail_first_for and text not in self.failed:
                    self.failed.add(text)
                    return httpx.Response(500, json={'error': {'message': 'boom', 'type': 'server_error'}})
                return self.envelope(self.coach_for.get(text, {'improved_text': text, 'notes': [], 'meaning_preserved': True, 'adds_information': False}))
            return self.envelope(self.check_for.get((user['original'], user['improved']), {'same_meaning': True}))
        finally:
            self.in_flight -= 1

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def run(tmp_path, server, **overrides):
    out = tmp_path / 'run'
    env = overrides.pop('env', {})
    return asyncio.run(runner.run_live(approval(**overrides), out, env=env, http_client=server.client(), now=NOW)), out


# ------------------------------------------------------------------------------------------------ fixtures and scoring
def test_held_out_fixtures_are_well_formed_fresh_and_self_consistent():
    cases = runner.load_cases()
    dev = {c['source'].strip().lower() for c in runner.load_cases(runner.DEV_FIXTURES)}
    assert 50 <= len(cases) <= 70 and len({c['id'] for c in cases}) == len(cases)
    assert not {c['source'].strip().lower() for c in cases} & dev          # disjoint from the development set
    assert {'grammar_tense', 'natural', 'negation', 'modality', 'quantity', 'comparison', 'certainty', 'causality', 'named_entity',
            'plausible_wrong_fact', 'vocabulary', 'uncertain', 'too_short', 'too_long', 'learner_corrected'} <= {c['category'] for c in cases}
    for case in cases:
        assert case['expect'] in ('accept', 'accept_unchanged', 'unavailable')
        if case['expect'] == 'unavailable':
            assert runner.eligibility_of(case)[0] is None
            continue
        assert runner.eligibility_of(case)[0] is not None
        # The oracle must accept its own reference answers: a fixture that fails its own checks cannot measure anything.
        for reference in case['references']:
            verdict = runner.score(case, reference)
            assert verdict['invariants_ok'], (case['id'], reference, verdict)
            assert verdict['fixes_ok'] or not case['must_fix'], (case['id'], reference)
        # ...and must notice the original error when fixes are required.
        if case['must_fix']:
            assert not runner.score(case, case['source'])['fixes_ok'], case['id']


def test_scoring_is_independent_of_the_validator_and_catches_silent_fact_fixes():
    cases = {c['id']: c for c in runner.load_cases()}
    wrong_fact = cases['h44']
    assert runner.score(wrong_fact, 'Paris is the capital of Germany and it has many museums')['invariants_ok'] is True
    fixed = runner.score(wrong_fact, 'Paris is the capital of France and it has many museums')
    assert fixed['invariants_ok'] is False and set(fixed['violations']) == {'missing:Germany', 'added:France'}
    assert runner.score(cases['h21'], "He doesn't like the new office because it is too noisy")['invariants_ok'] is True
    assert runner.score(cases['h21'], 'He likes the new office because it is too noisy')['invariants_ok'] is False       # negation lost
    assert runner.score(cases['h34'], 'It will definitely rain tomorrow')['invariants_ok'] is False                        # hedge strengthened
    assert runner.f1('a b c', 'a b c') == 1 and runner.f1('a b', 'c d') == 0 and 0 < runner.f1('a b c', 'a b d') < 1


@pytest.mark.parametrize('expect,outcome,invariants,quality,expected', [
    ('accept', 'accepted', True, True, 'accepted_ok'), ('accept', 'accepted', False, False, 'FALSE_ACCEPT'),
    ('accept', 'rejected', True, True, 'false_rejection'), ('accept', 'rejected', True, False, 'rejected_poor_candidate'),
    ('accept', 'rejected', False, False, 'correctly_rejected'), ('accept_unchanged', 'accepted_unchanged', True, True, 'unchanged_ok'),
    ('accept_unchanged', 'accepted', True, True, 'unneeded_edit_accepted'), ('accept_unchanged', 'rejected', True, True, 'unneeded_edit_rejected'),
    ('accept', 'failed', None, None, 'provider_error'), ('unavailable', 'unavailable', None, None, 'blocked_ok'),
])
def test_classification_table(expect, outcome, invariants, quality, expected):
    verdict = {'invariants_ok': invariants, 'quality_good': quality}
    assert runner.classify({'expect': expect}, outcome, verdict, 'x') == expected


# ------------------------------------------------------------------------------------------------ the plan
def test_plan_reports_model_counts_cost_categories_and_expected_outputs_without_any_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('plan must not use the network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    plan = runner.plan(repeats=1)
    assert plan['model'] == MODEL and plan['prompt_version'] == runner.PROMPT_VERSION and plan['executes_nothing'] is True
    assert plan['credentials_loaded'] is False and plan['network_calls'] == 0 and 'PROPOSAL ONLY' in plan['status']
    assert plan['fixtures'] == len(runner.load_cases()) and plan['cases_sent_to_a_provider'] + plan['cases_blocked_before_any_provider_call'] == plan['fixtures']
    assert plan['max_requests'] == plan['cases_sent_to_a_provider'] * 3 <= runner.MAX_REQUESTS
    assert 0 < plan['worst_case_reservation_usd'] <= runner.HARD_CAP_USD and plan['hard_cap_usd'] == 1.0
    assert plan['sdk_max_retries'] == 0 and plan['concurrency'] == 1 and sum(plan['categories'].values()) == plan['fixtures']
    assert len(plan['expected_outputs']) == plan['fixtures'] and all('references' in e and 'must_preserve' in e for e in plan['expected_outputs'])
    assert plan['approval_requirements']['approved'] is False                # the template is never an approval
    assert runner.main(['plan']) == 0


# ------------------------------------------------------------------------------------------------ the approval gate
@pytest.mark.parametrize('change,fragment', [
    ({'approved': False}, 'approved must be true'), ({'approved': 'yes'}, 'approved must be true'),
    ({'run_id': ''}, 'run_id is required'), ({'approved_by': '<owner>'}, 'approved_by is required'),
    ({'expires_at': '2026-10-22T11:00:00Z'}, 'expired'), ({'expires_at': '2026-10-23T10:00:00'}, 'expired or has no timezone'),
    ({'expires_at': 'tomorrow'}, 'valid ISO-8601'), ({'model': 'gpt-4.1-mini'}, 'model must be exactly'),
    ({'prompt_version': 'v1'}, 'prompt_version differs'), ({'fixtures_sha256': '0' * 64}, 'fixtures_sha256'),
    ({'prices_usd_per_million': {'input': .5, 'output': 2.0}}, 'prices differ'), ({'max_usd': 1.01}, 'max_usd'), ({'max_usd': 0}, 'max_usd'),
    ({'max_usd': True}, 'max_usd'), ({'max_usd': '1'}, 'max_usd'), ({'max_requests': 5}, 'max_requests'), ({'max_requests': 401}, 'max_requests'),
    ({'repeats': 2}, 'repeats differs'),
])
def test_every_approval_defect_is_refused_before_anything_runs(tmp_path, change, fragment):
    problems = runner.validate_approval(approval(**change), runner.plan(repeats=1), NOW)
    assert any(fragment in problem for problem in problems), problems
    with pytest.raises(runner.RefusedToRun, match='approval refused'):
        asyncio.run(runner.run_live(approval(**change), tmp_path / 'run', env={}, http_client=Server().client(), now=NOW))
    assert not (tmp_path / 'run').exists()                      # no ledger, no files: nothing ran


def test_a_valid_approval_has_no_problems_and_the_template_is_not_one():
    assert runner.validate_approval(approval(), runner.plan(repeats=1), NOW) == []
    assert runner.validate_approval(runner.approval_template(1, 1.0), runner.plan(repeats=1), NOW) != []
    assert runner.validate_approval('approved', runner.plan(repeats=1), NOW) == ['approval is not a JSON object']


def test_nothing_is_read_or_built_before_the_approval_validates(tmp_path, monkeypatch):
    accessed, built = [], []

    class Env(dict):
        def get(self, key, default=None):
            accessed.append(key)
            return super().get(key, default)
    monkeypatch.setattr(runner, 'GuardedProvider', lambda *a, **k: built.append(1))
    with pytest.raises(runner.RefusedToRun):
        asyncio.run(runner.run_live(approval(approved=False), tmp_path / 'run', env=Env(OPENAI_API_KEY='sk-secret'), now=NOW))
    assert 'OPENAI_API_KEY' not in accessed and built == []


def test_the_cli_refuses_without_a_valid_approval_and_exits_nonzero(tmp_path, capsys):
    template = tmp_path / 'approval.json'
    template.write_text(json.dumps(runner.approval_template(1, 1.0)))
    assert runner.main(['run', '--approval', str(template), '--out', str(tmp_path / 'out')]) == 2
    assert 'REFUSED' in capsys.readouterr().err and not (tmp_path / 'out').exists()
    assert runner.main(['run', '--approval', str(tmp_path / 'missing.json'), '--out', str(tmp_path / 'out')]) == 2


@pytest.mark.parametrize('variable', ['AUDLI_DATABASE_URL', 'DATABASE_URL', 'AUDLI_TEST_POSTGRES_URL', 'SUPABASE_ACCESS_TOKEN', 'AUDLI_SUPABASE_URL'])
def test_a_database_or_production_variable_in_the_environment_blocks_the_run(tmp_path, variable):
    with pytest.raises(runner.RefusedToRun, match='database or production variables'):
        asyncio.run(runner.run_live(approval(), tmp_path / 'run', env={variable: 'postgresql://example/production'}, http_client=Server().client(), now=NOW))
    assert not (tmp_path / 'run').exists()


def test_a_missing_credential_is_refused_when_no_transport_is_injected(tmp_path):
    with pytest.raises(runner.RefusedToRun, match='OPENAI_API_KEY is not set'):
        asyncio.run(runner.run_live(approval(), tmp_path / 'run', env={}, now=NOW))


# ------------------------------------------------------------------------------------------------ a complete mock run
def test_a_complete_run_reports_exact_model_requests_cost_latency_and_quality(tmp_path):
    server = Server()
    report, out = run(tmp_path, server)
    summary = report['summary']
    plan = runner.plan(repeats=1)
    assert report['completed'] is True and report['cap_overrun'] is False and (out / 'results.json').exists() and (out / 'report.md').exists()
    assert summary['provider']['models_returned'] == [MODEL] and summary['provider']['requests'] == len(server.requests) <= plan['max_requests']
    # Cost is the sum of the reported usage at the stated prices, recomputed independently.
    ledger = [json.loads(line) for line in (out / 'ledger.jsonl').read_text().splitlines()]
    results = [e for e in ledger if e['event'] == 'result']
    assert len(results) == len(server.requests) and all(e['model'] == MODEL for e in results)
    expected = sum(e['input_tokens'] * runner.PRICE_IN / 1e6 + e['output_tokens'] * runner.PRICE_OUT / 1e6 for e in results)
    assert summary['cost_usd']['charged_total'] == pytest.approx(expected, rel=1e-6) and expected < runner.HARD_CAP_USD
    assert summary['provider']['input_tokens'] == 1500 * len(results) and summary['latency_ms']['per_call']['p50'] is not None
    # Ineligible answers never reach the provider.
    assert summary['classifications']['blocked_ok'] == plan['cases_blocked_before_any_provider_call']
    assert sum(summary['classifications'].values()) == len(report['records']) == len(runner.load_cases())
    assert 'Live evaluation run-1' in (out / 'report.md').read_text() and 'False accepts' in (out / 'report.md').read_text()


def test_the_provider_only_ever_sees_the_learners_text_and_fixed_instructions(tmp_path):
    server = Server()
    run(tmp_path, server)
    for user in server.requests:
        assert set(user) <= {'learner_text', 'formatting_reminder', 'original', 'improved'}
    sources = {c['source'] for c in runner.load_cases()}
    assert all(user.get('learner_text') in sources for user in server.requests if 'learner_text' in user)
    blob = json.dumps(server.requests)
    for case in runner.load_cases():
        assert not any(reference in blob for reference in case['references'] if reference != case['source'] and len(reference) > 20)   # expected outputs never leak to the model


def test_a_meaning_change_the_validator_cannot_see_is_reported_as_a_false_accept(tmp_path, monkeypatch):
    source = 'The team repaired the database problem fast yesterday'
    swapped = 'The team repaired the network problem fast yesterday'
    case = {'id': 'x1', 'category': 'synonym_swap', 'source': source, 'references': [source], 'must_preserve': ['database'], 'must_fix': [], 'must_not_add': [], 'expect': 'accept'}
    monkeypatch.setattr(runner, 'load_cases', lambda path=runner.FIXTURES: [case] if path == runner.FIXTURES else runner.json.loads(path.read_text()))
    server = Server(coach={source: {'improved_text': swapped, 'notes': [{'kind': 'vocabulary', 'before': 'database', 'after': 'network', 'why': 'A more precise word.'}],
                                    'meaning_preserved': True, 'adds_information': False}})        # and the comparison model agrees (the failure mode)
    report, _ = run(tmp_path, server, max_requests=3)
    assert report['summary']['classifications'] == {'FALSE_ACCEPT': 1}
    assert report['summary']['meaning_preservation']['FALSE_ACCEPTS'] == ['x1']
    assert report['summary']['meaning_preservation']['caught_by_layer'] == {'accepted': 1}


# ------------------------------------------------------------------------------------------------ ledger, retries, concurrency
def test_every_request_is_on_disk_in_the_ledger_before_it_is_sent(tmp_path):
    out = tmp_path / 'run'
    server = Server(ledger_dir=out)
    run(tmp_path, server)
    assert server.ledger_snapshots == list(range(1, len(server.requests) + 1))       # each handler saw its own intent as the last line


def test_a_failed_request_is_never_resent_and_the_run_continues(tmp_path):
    first = next(c['source'] for c in runner.load_cases() if runner.eligibility_of(c)[0] is not None)
    server = Server(fail_first_for=first)
    report, out = run(tmp_path, server)
    assert [r.get('learner_text') for r in server.requests].count(first) == 1         # exactly one request: no SDK retry, no resend
    ledger = [json.loads(line) for line in (out / 'ledger.jsonl').read_text().splitlines()]
    unknown = [e for e in ledger if e['event'] == 'unknown']
    assert len(unknown) == 1 and unknown[0]['usd'] is None                              # usage unknown: its reservation stays charged
    assert report['summary']['classifications']['provider_error'] == 1 and report['summary']['provider']['failed_or_unknown'] == 1
    assert report['completed'] is True and report['summary']['cost_usd']['charged_total'] > 0


def test_requests_are_strictly_sequential(tmp_path):
    server = Server(delay=.005)
    run(tmp_path, server)
    assert server.max_in_flight == 1


def test_an_existing_ledger_blocks_a_second_run(tmp_path):
    run(tmp_path, Server())
    with pytest.raises(runner.RefusedToRun, match='existing ledger'):
        asyncio.run(runner.run_live(approval(), tmp_path / 'run', env={}, http_client=Server().client(), now=NOW))


# ------------------------------------------------------------------------------------------------ the spending cap
def test_the_run_stops_before_it_would_cross_the_cap_and_reports_a_partial_result(tmp_path):
    server = Server(usage=(1500, 700))
    report, out = run(tmp_path, server, max_usd=.01)
    ledger = [json.loads(line) for line in (out / 'ledger.jsonl').read_text().splitlines()]
    assert ledger[-1]['event'] == 'budget_stop' and report['completed'] is False
    assert report['summary']['cost_usd']['charged_total'] <= .01 and report['summary']['cost_usd']['budget_stopped'] is True
    assert report['cap_overrun'] is False and 0 < len(server.requests) < runner.plan(repeats=1)['max_requests']
    assert report['summary']['classifications'].get('not_run', 0) == 1                      # the case that could not be afforded is recorded


def test_usage_that_beats_the_reservation_ends_the_run_and_is_reported_as_an_overrun(tmp_path):
    report, out = run(tmp_path, Server(usage=(400_000, 700)), max_usd=.2)                    # absurd usage: ~$0.16 per request
    ledger = [json.loads(line) for line in (out / 'ledger.jsonl').read_text().splitlines()]
    assert report['cap_overrun'] is True and any(e['event'] == 'cap_overrun' for e in ledger) and report['completed'] is False
    assert len([e for e in ledger if e['event'] == 'intent']) <= 3                           # no further requests after the overrun


def test_the_cap_can_never_exceed_one_dollar_even_if_an_approval_says_so(tmp_path):
    assert runner.HARD_CAP_USD == 1.0
    assert runner.Ledger(tmp_path / 'a', 'r', 5.0, 10_000).cap == 1.0 and runner.Ledger(tmp_path / 'b', 'r', 5.0, 10_000).max_requests == runner.MAX_REQUESTS
    problems = runner.validate_approval(approval(max_usd=5.0), runner.plan(repeats=1), NOW)
    assert any('max_usd' in problem for problem in problems)


def test_the_worst_case_reservation_for_the_whole_plan_fits_the_cap():
    plan = runner.plan(repeats=1)
    assert plan['worst_case_reservation_usd'] < runner.HARD_CAP_USD / 2


# ------------------------------------------------------------------------------------------------ isolation from production
def test_the_runner_imports_no_storage_or_application_server_code():
    tree = ast.parse(Path(runner.__file__).read_text())
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module} | \
              {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not [m for m in modules if m.startswith(('sqlalchemy', 'psycopg', 'app.storage', 'app.main', 'app.repository', 'app.auth', 'app.account', 'app.lesson'))], modules
    source = Path(runner.__file__).read_text()
    assert 'create_engine' not in source and 'os.environ[' not in source


def test_the_live_runner_uses_the_production_prompts_and_decision_path():
    from app.services.openai_provider import COACH_PROMPT, MEANING_PROMPT
    assert runner.COACH_PROMPT is COACH_PROMPT and runner.MEANING_PROMPT is MEANING_PROMPT
    assert runner.coach.__module__ == 'app.improvement' and runner.OpenAIProvider.__module__ == 'app.services.openai_provider'
    assert issubclass(runner.GuardedProvider, runner.OpenAIProvider)


# ------------------------------------------------------------------------------------------------ the ceiling is a bound, not an average
def test_every_reservation_covers_one_token_per_utf8_byte_at_the_full_output_cap():
    """Byte-level BPE cannot emit more tokens than bytes, so this is a ceiling for any text, including non-ASCII."""
    from app.improvement import MAX_SOURCE_CHARS
    texts = [c['source'] for c in runner.load_cases()] + ['é' * MAX_SOURCE_CHARS, '日本語' * (MAX_SOURCE_CHARS // 3), '😀' * (MAX_SOURCE_CHARS // 2), '1234567890 ' * (MAX_SOURCE_CHARS // 11)]
    for text in texts:
        for call, system, cap in (('coach', runner.COACH_PROMPT, runner.COACH_MAX_OUTPUT_TOKENS), ('repair', runner.COACH_PROMPT, runner.COACH_MAX_OUTPUT_TOKENS), ('check', runner.MEANING_PROMPT, runner.CHECK_MAX_OUTPUT_TOKENS)):
            payload = json.dumps({'learner_text': text, 'original': text, 'improved': text})
            absolute_worst = runner.usd(len((system + payload).encode()), cap)               # one token per byte, no framing, full output
            assert runner.request_reservation(payload, call) >= absolute_worst


def test_a_provider_billing_the_absolute_maximum_on_every_request_still_cannot_pass_the_cap(tmp_path):
    """Usage at exactly each request's reserved worst case, every time: the run must end at or under $1 without an overrun."""
    class Maximum(Server):
        def envelope(self, payload):
            self._last = payload
            return super().envelope(payload)

        async def handle(self, request):
            body = json.loads(request.content)
            call = 'check' if 'original' in json.loads(body['input'][1]['content']) else 'coach'
            worst = runner.request_reservation(body['input'][1]['content'], call)
            ceiling = runner.COACH_MAX_OUTPUT_TOKENS if call == 'coach' else runner.CHECK_MAX_OUTPUT_TOKENS
            self.usage = (max(1, int((worst - runner.usd(0, ceiling)) * 1e6 / runner.PRICE_IN)), ceiling)   # exactly the reservation, rounded down
            return await super().handle(request)
    server = Maximum()
    report, out = run(tmp_path, server)
    assert report['cap_overrun'] is False and report['summary']['cost_usd']['charged_total'] <= runner.HARD_CAP_USD
    assert report['summary']['cost_usd']['charged_total'] <= runner.plan(repeats=1)['worst_case_reservation_usd'] + 1e-6


def test_the_whole_plan_at_its_worst_case_including_every_repair_fits_under_the_cap():
    plan = runner.plan(repeats=1)
    assert plan['worst_case_reservation_usd'] < runner.HARD_CAP_USD and plan['max_requests'] == plan['cases_sent_to_a_provider'] * 3
    assert runner.plan(repeats=2)['worst_case_reservation_usd'] == pytest.approx(2 * plan['worst_case_reservation_usd'], abs=1e-3)


# ------------------------------------------------------------------------------------------------ production behaviour is unchanged
def test_production_coaching_calls_send_no_output_cap_and_no_usage_sink():
    """The runner's caps are its own spend bounds: the production provider must not start truncating real learners' coaching."""
    from types import SimpleNamespace

    from app.config import Settings
    from app.improvement import LanguageCoaching, MeaningCheck
    from app.services.openai_provider import OpenAIProvider
    sent = []

    class Responses:
        async def parse(self, **kwargs):
            sent.append(kwargs)
            parsed = LanguageCoaching(improved_text='x', notes=[], meaning_preserved=True, adds_information=False) if kwargs['text_format'] is LanguageCoaching else MeaningCheck(same_meaning=True)
            return SimpleNamespace(output_parsed=parsed, usage=SimpleNamespace(input_tokens=1, output_tokens=1), model='m')

    class Client:
        responses = Responses()

        def with_options(self, **kwargs):
            return self
    provider = OpenAIProvider(Settings(_env_file=None, openai_api_key='sk-test'))
    provider.client = Client()
    asyncio.run(provider.coach_language('I goed to the shop yesterday'))
    asyncio.run(provider.check_meaning('I goed to the shop yesterday', 'I went to the shop yesterday'))
    assert len(sent) == 2 and all('max_output_tokens' not in call for call in sent) and all(call['store'] is False for call in sent)


def test_the_guarded_provider_always_applies_the_reserved_output_ceiling(tmp_path):
    server = Server()
    run(tmp_path, server)
    assert server.requests                                     # the handler itself asserts every body carried a max_output_tokens
    assert runner.COACH_MAX_OUTPUT_TOKENS >= 1000 and runner.CHECK_MAX_OUTPUT_TOKENS == 60


# ------------------------------------------------------------------------------------------------ the prepared approval file
def test_the_prepared_approval_file_is_complete_but_authorizes_nothing(tmp_path, capsys):
    path = Path(runner.__file__).resolve().parents[1] / 'docs' / 'AUD-77-LIVE-EVALUATION-APPROVAL.json'
    prepared = json.loads(path.read_text())
    plan = runner.plan(repeats=1)
    assert prepared['approved'] is False and not prepared['approved_by'] and not prepared['approved_at'] and not prepared['expires_at']
    assert prepared['max_usd'] == 1.0 <= runner.HARD_CAP_USD and prepared['repeats'] == 1 and prepared['max_requests'] == plan['max_requests']
    problems = runner.validate_approval(prepared, plan, NOW)
    assert 'approved must be true' in problems and 'approved_by is required' in problems     # as committed it is refused
    assert not any('model' in p or 'prompt_version' in p or 'fixtures_sha256' in p or 'prices' in p or 'max_usd' in p or 'max_requests' in p or 'repeats' in p for p in problems), problems
    # Filling in only the owner's fields makes it valid: nothing else can have drifted from the plan, the fixtures or the prices.
    owner = {**prepared, 'approved': True, 'approved_by': 'owner', 'approved_at': '2026-10-22T10:00:00Z', 'expires_at': '2026-10-23T10:00:00Z'}
    assert runner.validate_approval(owner, plan, NOW) == []
    with pytest.raises(runner.RefusedToRun, match='approval refused'):                         # the run itself refuses it before any credential or network use
        asyncio.run(runner.run_live(prepared, tmp_path / 'run', env={'OPENAI_API_KEY': 'sk-never-read'}, http_client=Server().client(), now=NOW))
    assert not (tmp_path / 'run').exists()
    assert runner.main(['run', '--approval', str(path), '--out', str(tmp_path / 'out')]) == 2
    assert not (tmp_path / 'out').exists()
