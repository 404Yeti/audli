"""AUD-77 LIVE evaluation runner for advisory Review language coaching: held-out synthetic ESL cases only.

EXECUTION IS NOT AUTHORIZED BY THIS FILE. `plan` (the default) makes no network call and reads no credential. `run` refuses
unless it is given an owner approval file that matches this exact plan; until then no provider is ever contacted.

  python -m scripts.run_review_improvement_eval plan [--repeats N] [--out FILE]
  python -m scripts.run_review_improvement_eval validate --approval FILE             # checks a completed file; reads no credential, runs nothing
  python -m scripts.run_review_improvement_eval run --approval FILE --out DIR      # only after separate owner approval

Guarantees enforced in code (and tested with a mock transport, never a real provider):
  * synthetic fixtures only (tests/fixtures/review_improvement_heldout.json, disjoint from the development set); no database,
    no learner data: the module imports no storage code and refuses to start if a database URL is in the environment;
  * a hard spending cap of $1.00 that an approval cannot raise; each request's worst case is reserved before it is sent and
    the run stops (never exceeds) when the next reservation would cross the cap;
  * zero automatic network retries (SDK max_retries=0), concurrency one, a per-request timeout, never a resend after an
    ambiguous failure, no resumption of an existing ledger;
  * every request is written to an fsync'd ledger BEFORE it is sent;
  * the credential is read only after the approval validates.

It evaluates BOTH correction quality (similarity to reference rewrites and required fixes) and meaning preservation
(invariants from the fixtures, checked on the model's raw output by code that is independent of the production validator).
"""
import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from app.config import Settings
from app.improvement import PROMPT_VERSION, coach, eligibility, rejection_layer
from app.services.openai_provider import COACH_PROMPT, MEANING_PROMPT, OpenAIProvider

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'review_improvement_heldout.json'
DEV_FIXTURES = FIXTURES.with_name('review_improvement_cases.json')
PINNED_MODEL = 'gpt-4.1-mini-2025-04-14'
# Standard-tier USD per 1M tokens for gpt-4.1-mini, read from OpenAI's pricing page on 2026-10-22. An approval must restate them;
# a mismatch (a price change) stops the run before any request.
PRICE_IN, PRICE_OUT = .40, 1.60
HARD_CAP_USD = 1.00
MAX_REQUESTS = 400
REQUEST_TIMEOUT_SECONDS = 30
MIN_CONFIDENCE = .65
FORBIDDEN_ENVIRONMENT = ('AUDLI_DATABASE_URL', 'DATABASE_URL', 'AUDLI_TEST_POSTGRES_URL', 'SUPABASE_ACCESS_TOKEN', 'AUDLI_SUPABASE_URL')
# Whole families, not a list that can fall behind: any application (AUDLI_*) or Supabase setting, anything naming a database URL, Postgres,
# a service-role key or the other provider, and the libpq connection variables. The one credential this run may use is OPENAI_API_KEY.
FORBIDDEN_PREFIXES = ('AUDLI_', 'SUPABASE', 'PG')
FORBIDDEN_FRAGMENTS = ('DATABASE_URL', 'POSTGRES', 'SERVICE_ROLE', 'ELEVENLABS')


def forbidden_environment(env) -> list[str]:
    """Names (never values) of set variables that could point this run at production data or another credential."""
    # Match on the NAME first: a variable that is not in a forbidden family (OPENAI_API_KEY included) is never even looked up.
    return sorted(name for name in list(env) if (name in FORBIDDEN_ENVIRONMENT or name.startswith(FORBIDDEN_PREFIXES)
                                                  or any(fragment in name.upper() for fragment in FORBIDDEN_FRAGMENTS)) and env.get(name))
NEGATION_WORDS = re.compile(r"\b(?:not|no|never|nobody|nothing|none|cannot|without|neither|nor)\b|n't", re.I)


class RefusedToRun(Exception):
    """A precondition failed before any request or credential read."""


class BudgetStop(Exception):
    """The next request could cross the spending cap."""


# ------------------------------------------------------------------------------------------ fixtures and the plan
def load_cases(path: Path = FIXTURES) -> list[dict]:
    return json.loads(path.read_text())


def fixtures_hash(path: Path = FIXTURES) -> str:
    return sha256(path.read_bytes()).hexdigest()


# Spend bounds that apply to this runner only; production coaching sets no output cap. 1,000 covers a 1,200-character source's
# rewrite plus two maximum-length notes with margin, so the cap bounds cost without truncating a legitimate answer.
COACH_MAX_OUTPUT_TOKENS = 1000
CHECK_MAX_OUTPUT_TOKENS = 60
FRAMING_TOKENS = 300  # request envelope, schema and role framing beyond the system prompt and data


def tokens(utf8_bytes: float) -> float:
    """Upper bound on input tokens: a byte-level BPE tokenizer never emits more tokens than UTF-8 bytes (1 token per byte),
    plus a framing allowance. This is a ceiling, not an average; real usage replaces it once reported."""
    return utf8_bytes + FRAMING_TOKENS


def usd(inputs: float, outputs: float) -> float:
    return inputs * PRICE_IN / 1e6 + outputs * PRICE_OUT / 1e6


def request_reservation(text: str, call: str) -> float:
    """Worst-case cost of one request: pessimistic input estimate plus the full output ceiling."""
    system = COACH_PROMPT if call in ('coach', 'repair') else MEANING_PROMPT
    ceiling = COACH_MAX_OUTPUT_TOKENS if call in ('coach', 'repair') else CHECK_MAX_OUTPUT_TOKENS
    return usd(tokens(len(system.encode()) + len(text.encode()) + 2400), ceiling)  # 2,400 bytes of strict-schema allowance; output is the hard max_output_tokens cap


def plan(cases: list[dict] | None = None, repeats: int = 1) -> dict:
    """Reservation arithmetic and expected outputs for a FUTURE approved run. Loads no credentials; makes no network call."""
    cases = cases if cases is not None else load_cases()
    sent = [c for c in cases if eligibility_of(c)[0] is not None]
    requests = len(sent) * repeats * 3  # coach + at most one repair + at most one comparison
    reserved = sum(request_reservation(c['source'], 'coach') * 2 + request_reservation(c['source'] + ' ' + c['source'], 'check') for c in sent) * repeats
    return {'harness': 'live-plan', 'executes_nothing': True, 'credentials_loaded': False, 'network_calls': 0,
            'status': 'PROPOSAL ONLY. A run needs a separate owner approval file matching this plan; none exists.',
            'model': PINNED_MODEL, 'prompt_version': PROMPT_VERSION, 'prices_usd_per_million': {'input': PRICE_IN, 'output': PRICE_OUT},
            'fixtures': len(cases), 'fixtures_sha256': fixtures_hash(), 'repeats': repeats,
            'cases_sent_to_a_provider': len(sent), 'cases_blocked_before_any_provider_call': len(cases) - len(sent),
            'max_requests': requests, 'request_ceiling': MAX_REQUESTS, 'concurrency': 1, 'sdk_max_retries': 0, 'timeout_seconds': REQUEST_TIMEOUT_SECONDS,
            'max_output_tokens': {'coach': COACH_MAX_OUTPUT_TOKENS, 'comparison': CHECK_MAX_OUTPUT_TOKENS},
            'worst_case_reservation_usd': round(reserved, 4), 'hard_cap_usd': HARD_CAP_USD,
            'categories': dict(sorted(Counter(c['category'] for c in cases).items())),
            'evaluates': ['correction quality (best token-F1 to a reference rewrite; required fixes present)',
                          'meaning preservation (fixture invariants on the model raw output, independent of the validator)',
                          'pipeline agreement: false accepts, false rejections, which layer caught each violation',
                          'cost (actual usage), latency (p50/p95 per call and per improvement), provider errors'],
            'expected_outputs': [{'id': c['id'], 'category': c['category'], 'source': c['source'] if len(c['source']) < 400 else c['source'][:400] + '...',
                                  'expect': c['expect'], 'references': c['references'][:3], 'must_preserve': c['must_preserve'],
                                  'must_fix': c['must_fix'], 'must_not_add': c['must_not_add']} for c in cases],
            'approval_requirements': approval_template(requests, reserved)}


def approval_template(requests: int, reserved: float) -> dict:
    """What an approval file must contain. This is a TEMPLATE, not an approval: `approved` is false."""
    return {'approved': False, 'approved_by': '<owner>', 'approved_at': '<ISO-8601 time>', 'expires_at': '<ISO-8601 time>', 'run_id': '<unique id>',
            'model': PINNED_MODEL, 'prompt_version': PROMPT_VERSION, 'fixtures_sha256': fixtures_hash(),
            'prices_usd_per_million': {'input': PRICE_IN, 'output': PRICE_OUT}, 'max_usd': f'<= {HARD_CAP_USD} (cannot exceed the in-code cap)',
            'max_requests': f'>= {requests} and <= {MAX_REQUESTS}', 'repeats': 1, 'worst_case_reservation_usd': round(reserved, 4)}


def eligibility_of(case: dict):
    transcription = {'text': case.get('recognized_text') or case['source'], 'confidence': .93, 'uncertainty': [], 'source': 'openai', **case.get('transcription', {})}
    return eligibility(case['source'], transcription, case['source'], MIN_CONFIDENCE)


# ------------------------------------------------------------------------------------------ approval
def validate_approval(approval, expected_plan: dict, now: datetime) -> list[str]:
    """Reasons to refuse. Empty means the approval matches this exact plan."""
    problems = []
    if not isinstance(approval, dict):
        return ['approval is not a JSON object']
    if approval.get('approved') is not True:
        problems.append('approved must be true')
    for key in ('approved_by', 'approved_at', 'expires_at', 'run_id'):
        if not isinstance(approval.get(key), str) or not approval[key].strip() or approval[key].startswith('<'):
            problems.append(f'{key} is required')
    try:
        expires = datetime.fromisoformat(str(approval.get('expires_at')).replace('Z', '+00:00'))
        if expires.tzinfo is None or expires <= now:
            problems.append('the approval has expired or has no timezone')
    except ValueError:
        problems.append('expires_at is not a valid ISO-8601 time')
    if approval.get('model') != PINNED_MODEL:
        problems.append(f'model must be exactly {PINNED_MODEL}')
    if approval.get('prompt_version') != PROMPT_VERSION:
        problems.append('prompt_version differs from this runner')
    if approval.get('fixtures_sha256') != expected_plan['fixtures_sha256']:
        problems.append('fixtures_sha256 does not match the held-out fixtures')
    if approval.get('prices_usd_per_million') != {'input': PRICE_IN, 'output': PRICE_OUT}:
        problems.append('prices differ from the runner: re-verify and update both')
    cap = approval.get('max_usd')
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or not 0 < cap <= HARD_CAP_USD:
        problems.append(f'max_usd must be a number in (0, {HARD_CAP_USD}]')
    limit = approval.get('max_requests')
    if not isinstance(limit, int) or isinstance(limit, bool) or not expected_plan['max_requests'] <= limit <= MAX_REQUESTS:
        problems.append(f"max_requests must be an integer in [{expected_plan['max_requests']}, {MAX_REQUESTS}]")
    if approval.get('repeats') != expected_plan['repeats']:
        problems.append('repeats differs from the plan')
    return problems


# ------------------------------------------------------------------------------------------ ledger and guarded provider
class Ledger:
    """Durable, append-only, single-run: intent is on disk before a request leaves the process."""
    def __init__(self, directory: Path, run_id: str, cap_usd: float, max_requests: int):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'ledger.jsonl'
        if self.path.exists():
            raise RefusedToRun('an existing ledger must be reviewed by the owner; automatic resumption is prohibited')
        self.cap, self.max_requests, self.run_id = min(cap_usd, HARD_CAP_USD), min(max_requests, MAX_REQUESTS), run_id
        self.charged, self.requests, self.seq, self.stopped, self.overrun = 0., 0, 0, False, False

    def _append(self, record: dict) -> None:
        with self.path.open('a') as file:
            file.write(json.dumps({'run_id': self.run_id, **record}) + '\n'); file.flush(); os.fsync(file.fileno())

    def reserve(self, case: str, call: str, worst: float) -> int:
        if self.stopped or self.requests >= self.max_requests or self.charged + worst > self.cap + 1e-12:
            self.stopped = True
            self._append({'event': 'budget_stop', 'case': case, 'call': call, 'charged_usd': round(self.charged, 6), 'worst_case_usd': round(worst, 6), 'cap_usd': self.cap})
            raise BudgetStop()
        self.seq += 1; self.requests += 1
        self.charged += worst            # Held at the worst case until the real usage is known.
        self._append({'event': 'intent', 'seq': self.seq, 'case': case, 'call': call, 'reserved_usd': round(worst, 6)})
        return self.seq

    def settle(self, seq: int, worst: float, actual: float | None, **fields) -> None:
        if actual is not None:
            self.charged += actual - worst   # Real usage replaces the reservation.
        self._append({'event': 'result' if actual is not None else 'unknown', 'seq': seq, 'charged_usd': round(self.charged, 6),
                      'usd': None if actual is None else round(actual, 6), **fields})   # Unknown usage keeps its reservation.
        if self.charged > self.cap + 1e-12:
            self.overrun = self.stopped = True   # Real usage beat the pessimistic reservation: stop and report it.
            self._append({'event': 'cap_overrun', 'charged_usd': round(self.charged, 6), 'cap_usd': self.cap})


class GuardedProvider(OpenAIProvider):
    """The production provider, with a spending guard around every request and the raw model outputs recorded."""
    def __init__(self, settings, ledger: Ledger, http_client=None):
        super().__init__(settings)
        if http_client is not None:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(api_key='sk-evaluation-mock', max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS, http_client=http_client)
        else:
            self.client = self.client.with_options(max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)
        self.ledger, self.case, self.calls, self.raw, self.usage_sink = ledger, None, [], {'coach': [], 'check': []}, []

    def begin(self, case_id: str):
        self.case, self.calls, self.raw = case_id, [], {'coach': [], 'check': []}

    async def structured(self, schema, system: str, data: dict, retries=None, sensitive=False, max_output_tokens=None, usage=None):
        call = 'check' if schema.__name__ == 'MeaningCheck' else 'repair' if 'formatting_reminder' in data else 'coach'
        worst = request_reservation(json.dumps(data), call)
        max_output_tokens = CHECK_MAX_OUTPUT_TOKENS if call == 'check' else COACH_MAX_OUTPUT_TOKENS   # always the reserved ceiling
        seq = self.ledger.reserve(self.case, call, worst)
        started, usage_list = time.perf_counter(), []
        try:
            result = await super().structured(schema, system, data, retries=0, sensitive=True, max_output_tokens=max_output_tokens, usage=usage_list)
        except BaseException as error:
            self.ledger.settle(seq, worst, None, case=self.case, call=call, error_type=type(error).__name__, latency_ms=round((time.perf_counter() - started) * 1000))
            self.calls.append({'call': call, 'ok': False, 'error_type': type(error).__name__, 'latency_ms': (time.perf_counter() - started) * 1000, 'usd': worst})
            raise
        reported = usage_list[0] if usage_list else {}
        if isinstance(reported.get('input_tokens'), int) and isinstance(reported.get('output_tokens'), int):
            actual = usd(reported['input_tokens'], reported['output_tokens'])   # May exceed the reservation: recorded honestly.
        else:
            actual = None   # No usage reported: keep the worst-case reservation as the charge.
        latency = (time.perf_counter() - started) * 1000
        self.ledger.settle(seq, worst, actual, case=self.case, call=call, ok=True, latency_ms=round(latency), model=reported.get('model'),
                           input_tokens=reported.get('input_tokens'), output_tokens=reported.get('output_tokens'))
        self.calls.append({'call': call, 'ok': True, 'latency_ms': latency, 'usd': actual if actual is not None else worst, 'model': reported.get('model'),
                           'input_tokens': reported.get('input_tokens'), 'output_tokens': reported.get('output_tokens')})
        (self.raw['check'] if call == 'check' else self.raw['coach']).append(result.model_dump(mode='json'))
        return result


# ------------------------------------------------------------------------------------------ scoring (independent of the validator)
def words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[^\W_]+(?:['’][^\W_]+)?", text.replace('’', "'"))]


def f1(candidate: str, reference: str) -> float:
    a, b = Counter(words(candidate)), Counter(words(reference))
    overlap = sum((a & b).values())
    if not overlap:
        return 1.0 if not a and not b else 0.0
    precision, recall = overlap / sum(a.values()), overlap / sum(b.values())
    return 2 * precision * recall / (precision + recall)


def contains(text: str, phrase: str) -> bool:
    if phrase == '@NEGATION':
        return bool(NEGATION_WORDS.search(text.replace('’', "'")))
    return re.search(r'(?<![\w])' + re.escape(phrase.lower().replace('’', "'")) + r'(?![\w])', text.lower().replace('’', "'")) is not None


def score(case: dict, candidate: str | None) -> dict:
    """Quality and meaning-preservation verdicts for a raw model candidate, from the fixture's own expectations."""
    if candidate is None:
        return {'quality_good': False, 'best_f1': None, 'fixes_ok': None, 'invariants_ok': None, 'violations': []}
    best = max(f1(candidate, reference) for reference in case['references'])
    fixes_ok = all(contains(candidate, fix['add']) if fix['add'] else True for fix in case['must_fix']) and \
        all(not contains(candidate, fix['remove']) for fix in case['must_fix'] if fix['remove'])
    violations = [f'missing:{p}' for p in case['must_preserve'] if not contains(candidate, p)] + [f'added:{p}' for p in case['must_not_add'] if contains(candidate, p)]
    return {'quality_good': best >= .85 and fixes_ok, 'best_f1': round(best, 3), 'fixes_ok': fixes_ok, 'invariants_ok': not violations, 'violations': violations}


def classify(case: dict, outcome: str, scored: dict, candidate: str | None) -> str:
    if outcome == 'unavailable':
        return 'blocked_ok' if case['expect'] == 'unavailable' else 'blocked_unexpectedly'
    if outcome == 'failed':
        return 'provider_error'
    if outcome == 'budget_stop':
        return 'not_run'
    if case['expect'] == 'accept_unchanged' and outcome in ('accepted', 'accepted_unchanged'):
        return 'unchanged_ok' if outcome == 'accepted_unchanged' else 'unneeded_edit_accepted'
    accepted = outcome in ('accepted', 'accepted_unchanged')
    if not accepted and case['expect'] == 'accept_unchanged':
        return 'unneeded_edit_rejected'
    if accepted:
        return 'accepted_ok' if scored['invariants_ok'] else 'FALSE_ACCEPT'
    if scored['invariants_ok'] and scored['quality_good']:
        return 'false_rejection'
    return 'rejected_poor_candidate' if scored['invariants_ok'] else 'correctly_rejected'


def quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {'p50': None, 'p95': None, 'max': None}
    return {'p50': round(statistics.median(ordered)), 'p95': round(ordered[min(len(ordered) - 1, math.ceil(.95 * len(ordered)) - 1)]), 'max': round(ordered[-1])}


def summarize(records: list[dict], ledger: Ledger | None = None) -> dict:
    by_class, by_category = Counter(r['classification'] for r in records), defaultdict(Counter)
    for r in records:
        by_category[r['category']][r['classification']] += 1
    sent = [r for r in records if r['calls']]
    raw_violations = [r for r in sent if r['score']['invariants_ok'] is False]
    caught = Counter(r['layer'] or 'accepted' for r in raw_violations)
    calls = [c for r in records for c in r['calls']]
    ok_calls = [c for c in calls if c['ok']]
    per_improvement = [sum(c['latency_ms'] for c in r['calls']) for r in sent]
    costs = [sum(c['usd'] for c in r['calls']) for r in sent]
    return {'attempts_recorded': len(records), 'classifications': dict(sorted(by_class.items())),
            'by_category': {k: dict(sorted(v.items())) for k, v in sorted(by_category.items())},
            'meaning_preservation': {'raw_candidates_checked': len(sent), 'raw_candidates_violating_invariants': len(raw_violations),
                'violation_rate': round(len(raw_violations) / len(sent), 3) if sent else None, 'caught_by_layer': dict(sorted(caught.items())),
                'FALSE_ACCEPTS': [r['id'] for r in records if r['classification'] == 'FALSE_ACCEPT']},
            'correction_quality': {'fix_cases_scored': sum(1 for r in sent if r['expect'] == 'accept'), 'good_candidates': sum(1 for r in sent if r['expect'] == 'accept' and r['score']['quality_good']),
                'mean_best_f1': round(statistics.mean([r['score']['best_f1'] for r in sent if r['expect'] == 'accept' and r['score']['best_f1'] is not None] or [0]), 3),
                'pipeline_accepted_ok': by_class['accepted_ok'], 'false_rejections': [r['id'] for r in records if r['classification'] == 'false_rejection'],
                'unchanged_ok': by_class['unchanged_ok'], 'unneeded_edits': [r['id'] for r in records if r['classification'] == 'unneeded_edit_accepted']},
            'provider': {'requests': len(calls), 'failed_or_unknown': len(calls) - len(ok_calls), 'repairs_used': sum(1 for r in records if r['repaired']),
                'models_returned': sorted({c['model'] for c in ok_calls if c.get('model')}),
                'input_tokens': sum(c.get('input_tokens') or 0 for c in ok_calls), 'output_tokens': sum(c.get('output_tokens') or 0 for c in ok_calls)},
            'latency_ms': {'per_call': quantiles([c['latency_ms'] for c in ok_calls]), 'per_improvement': quantiles(per_improvement)},
            'cost_usd': {'charged_total': round(ledger.charged, 6) if ledger else round(sum(costs), 6), 'cap': ledger.cap if ledger else HARD_CAP_USD,
                'mean_per_attempt': round(statistics.mean(costs), 6) if costs else 0, 'p95_per_attempt': round(sorted(costs)[min(len(costs) - 1, math.ceil(.95 * len(costs)) - 1)], 6) if costs else 0,
                'budget_stopped': bool(ledger and ledger.stopped), 'cap_overrun': bool(ledger and ledger.overrun)}}


# ------------------------------------------------------------------------------------------ the run
async def run_case(case: dict, provider: GuardedProvider, repetition: int) -> dict:
    text, reason = eligibility_of(case)
    record = {'id': case['id'], 'category': case['category'], 'repetition': repetition, 'expect': case['expect'], 'source': case['source'][:400],
              'references': case['references'][:3], 'calls': [], 'repaired': False, 'layer': None, 'code': reason, 'candidate': None, 'raw': None}
    if text is None:
        record.update(outcome='unavailable', score=score(case, None), classification=classify(case, 'unavailable', score(case, None), None))
        return record
    provider.begin(case['id'])
    try:
        decision = await asyncio.wait_for(coach(provider, text), REQUEST_TIMEOUT_SECONDS * 3)
        candidate = provider.raw['coach'][-1]['improved_text'] if provider.raw['coach'] else None
        outcome = 'accepted_unchanged' if decision.status == 'ready' and decision.improved_text == text.strip() else 'accepted' if decision.status == 'ready' else 'rejected'
        record.update(code=decision.rejection, layer=rejection_layer(decision.rejection), repaired=decision.repaired)
    except BudgetStop as stop:
        record.update(outcome='budget_stop', calls=list(provider.calls), raw=provider.raw, score=score(case, None), classification='not_run')
        stop.record = record
        raise
    except Exception as error:  # Provider/transport errors: recorded, never retried.
        candidate, outcome = (provider.raw['coach'][-1]['improved_text'] if provider.raw['coach'] else None), 'failed'
        record.update(code=type(error).__name__)
    scored = score(case, candidate)
    record.update(outcome=outcome, calls=list(provider.calls), raw=provider.raw, candidate=candidate, score=scored, classification=classify(case, outcome, scored, candidate))
    return record


async def run_live(approval: dict, out_dir: Path, env=None, http_client=None, now: datetime | None = None) -> dict:
    """Execute the approved evaluation. Every refusal happens BEFORE a credential is read or a client is built."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    cases = load_cases()
    expected = plan(cases, repeats=approval.get('repeats', 1) if isinstance(approval, dict) else 1)
    problems = validate_approval(approval, expected, now)
    if problems:
        raise RefusedToRun('approval refused: ' + '; '.join(problems))
    present = forbidden_environment(env)
    if present:
        raise RefusedToRun('refusing to start with database, production or other-provider variables in the environment (names only): ' + ', '.join(present))
    if {c['source'].strip().lower() for c in cases} & {c['source'].strip().lower() for c in load_cases(DEV_FIXTURES)}:
        raise RefusedToRun('the held-out set overlaps the development set')
    ledger = Ledger(out_dir, approval['run_id'], float(approval['max_usd']), int(approval['max_requests']))
    key = env.get('OPENAI_API_KEY')       # Read only now, after every check passed.
    if not key and http_client is None:
        raise RefusedToRun('OPENAI_API_KEY is not set')
    settings = Settings(llm_model=approval['model'], OPENAI_API_KEY=key or 'sk-evaluation-mock', _env_file=None)
    provider = GuardedProvider(settings, ledger, http_client)
    records, stopped = [], False
    try:
        for repetition in range(approval['repeats']):
            for case in cases:
                try:
                    records.append(await run_case(case, provider, repetition))
                except BudgetStop as stop:
                    records.append(stop.record)
                    stopped = True
                    break
            if stopped or ledger.stopped:
                stopped = True
                break
    finally:
        await provider.client.close()
    report = {'run_id': approval['run_id'], 'model_requested': approval['model'], 'prompt_version': PROMPT_VERSION, 'fixtures_sha256': expected['fixtures_sha256'],
              'approved_by': approval['approved_by'], 'completed': not stopped, 'cap_overrun': ledger.overrun, 'summary': summarize(records, ledger), 'records': records}
    (out_dir / 'results.json').write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    (out_dir / 'report.md').write_text(render(report))
    return report


def render(report: dict) -> str:
    s = report['summary']
    m, q, p, c, l = s['meaning_preservation'], s['correction_quality'], s['provider'], s['cost_usd'], s['latency_ms']
    lines = [f"# Live evaluation {report['run_id']}", '', f"Model requested `{report['model_requested']}`; returned {p['models_returned']}; prompt {report['prompt_version']}; fixtures {report['fixtures_sha256'][:12]}; approved by {report['approved_by']}.",
             f"Completed: {report['completed']}. Requests {p['requests']} (failed/unknown {p['failed_or_unknown']}); repairs used {p['repairs_used']}; tokens in/out {p['input_tokens']}/{p['output_tokens']}.",
             f"Cost charged ${c['charged_total']} of cap ${c['cap']} (mean ${c['mean_per_attempt']} per attempt, p95 ${c['p95_per_attempt']}); budget stop: {c['budget_stopped']}.",
             f"Latency ms per call {l['per_call']}; per improvement {l['per_improvement']}.", '',
             f"## Meaning preservation\nRaw candidates violating a fixture invariant: {m['raw_candidates_violating_invariants']}/{m['raw_candidates_checked']}; caught by layer {m['caught_by_layer']}.\n**False accepts: {m['FALSE_ACCEPTS'] or 'none'}**", '',
             f"## Correction quality\nGood candidates {q['good_candidates']}/{q['fix_cases_scored']} (mean best F1 {q['mean_best_f1']}); accepted and correct {q['pipeline_accepted_ok']}; false rejections {q['false_rejections'] or 'none'}; natural answers left unchanged {q['unchanged_ok']}; unneeded edits {q['unneeded_edits'] or 'none'}.", '',
             '## Classifications\n' + '\n'.join(f'- {k}: {v}' for k, v in s['classifications'].items()), '', '## By category']
    lines += [f'- {k}: {v}' for k, v in s['by_category'].items()]
    return '\n'.join(lines) + '\n'


def validate_command(approval_path: Path, env=None, now: datetime | None = None) -> tuple[int, str]:
    """Check a completed approval file against the exact plan, the clock and the environment. Reads no credential, makes no network
    call and creates nothing. Exit 0 only when `run` would pass its own approval and environment checks."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    try:
        approval = json.loads(Path(approval_path).read_text())
    except (OSError, ValueError) as error:
        return 2, f'NOT VALID: cannot read the approval file as JSON ({type(error).__name__})'
    expected = plan(repeats=approval.get('repeats', 1) if isinstance(approval, dict) else 1)
    problems = validate_approval(approval, expected, now)
    present = forbidden_environment(env)
    if present:
        problems.append('this shell has database, production or other-provider variables set (names only): ' + ', '.join(present))
    summary = (f"model {expected['model']}, prompt {expected['prompt_version']}, {expected['cases_sent_to_a_provider']} cases to the provider, at most "
               f"{approval.get('max_requests') if isinstance(approval, dict) else '?'} requests, cap ${approval.get('max_usd') if isinstance(approval, dict) else '?'} "
               f"(worst-case reservation ${expected['worst_case_reservation_usd']})")
    if problems:
        return 2, 'NOT VALID (nothing was run, no credential was read):\n  - ' + '\n  - '.join(problems) + '\nPlan: ' + summary
    return 0, f"VALID: this approval matches the exact plan and the environment. Nothing has been run and no credential was read.\nPlan: {summary}\nApproved by {approval['approved_by']}, expires {approval['expires_at']}."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')
    planner = sub.add_parser('plan'); planner.add_argument('--repeats', type=int, default=1); planner.add_argument('--out')
    runner = sub.add_parser('run'); runner.add_argument('--approval', required=True); runner.add_argument('--out', required=True)
    checker = sub.add_parser('validate'); checker.add_argument('--approval', required=True)
    args = parser.parse_args(argv)
    if args.command in (None, 'plan'):
        report = plan(repeats=getattr(args, 'repeats', 1))
        text = json.dumps(report, indent=1, ensure_ascii=False)
        if getattr(args, 'out', None):
            Path(args.out).write_text(text + '\n')
        print(text)
        return 0
    if args.command == 'validate':
        code, message = validate_command(Path(args.approval))
        print(message, file=sys.stderr if code else sys.stdout)
        return code
    try:
        approval = json.loads(Path(args.approval).read_text())
        report = asyncio.run(run_live(approval, Path(args.out)))
    except (RefusedToRun, OSError, ValueError) as error:
        print(f'REFUSED: {error}', file=sys.stderr)
        return 2
    print(render(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
