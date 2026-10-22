"""AUD-77 evaluation harness for advisory Review language coaching. MOCK ONLY: no network, no credentials, no provider.

Runs synthetic ESL responses through the exact production decision path (`app.improvement.eligibility` and
`app.improvement.coach`) with a scripted provider, and reports how the validation layers behave on candidates that are
known to be good, natural, meaning-changing, comprehension-"fixing", or failing. It measures the validator and the plumbing.
It does NOT measure how often a real model produces good candidates; that needs the owner-approved live run described by
`plan`, which this script deliberately cannot perform (there is no paid-execution mode).

  python -m scripts.evaluate_review_improvement mock [--out FILE]
  python -m scripts.evaluate_review_improvement plan [--repeats N] [--budget-usd X] [--out FILE]
"""
import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from app.improvement import LanguageCoaching, MeaningCheck, eligibility, coach, PROMPT_VERSION, REPAIR_REMINDER
from app.services.openai_provider import COACH_PROMPT, MEANING_PROMPT

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'review_improvement_cases.json'
MIN_CONFIDENCE = .65  # Settings.min_transcription_confidence default
MODEL = 'gpt-4.1-mini'
# Standard-tier USD per 1M tokens, read from OpenAI's pricing page on 2026-10-22. Recheck before any approved run.
PRICE_IN, PRICE_OUT = .40, 1.60
SDK_RETRIES = 2  # OpenAIProvider's client uses max_retries=2: a failing request can be sent up to three times.
LAYERS = {
    'meaning_not_preserved': '1 provider self-report', 'information_added': '1 provider self-report',
    'length_changed': '2 structure', 'too_many_notes': '2 structure', 'inconsistent_notes': '2 structure',
    'unexplained_changes': '2 structure', 'note_not_grounded': '2 structure', 'too_many_changes': '2 structure',
    'content_added': '2 structure', 'content_removed': '2 structure',
    'negation_changed': '3 meaning classes', 'modality_changed': '3 meaning classes', 'quantity_changed': '3 meaning classes',
    'comparison_changed': '3 meaning classes', 'certainty_changed': '3 meaning classes', 'causality_changed': '3 meaning classes',
    'entity_changed': '3 meaning classes', 'meaning_check_failed': '4 independent comparison',
}
SCHEMA_CHARS = {'coach': len(json.dumps(LanguageCoaching.model_json_schema())), 'check': len(json.dumps(MeaningCheck.model_json_schema()))}


def load_cases() -> list[dict]:
    return json.loads(FIXTURES.read_text())


class ScriptedProvider:
    """Returns the case's scripted candidate. Records everything it is sent so privacy can be asserted."""
    def __init__(self, case, lenient):
        self.case, self.lenient, self.sent, self.coach_calls, self.check_calls, self.returned = case, lenient, [], 0, 0, []

    async def coach_language(self, text, repair=None):
        self.coach_calls += 1
        self.sent.append(text)
        if repair is not None:
            self.sent.append(repair)
        error = self.case.get('error')
        if error == 'timeout':
            raise TimeoutError()
        if error == 'malformed':
            raise ValueError('Provider returned incomplete structured output')
        candidate = self.case['repair_candidate'] if repair is not None and self.case.get('repair_candidate') else self.case['candidate']
        self.returned.append(candidate)
        return LanguageCoaching.model_validate(candidate)

    async def check_meaning(self, original, improved):
        self.check_calls += 1
        self.sent.extend([original, improved])
        return MeaningCheck(same_meaning=True if self.lenient else bool(self.case.get('verifier_same_meaning', True)))


def tokens(characters: float) -> float:
    return characters / 4  # Rough English/JSON heuristic (about +/-25%); a live run reports real usage.


def estimate(case, provider, outcome) -> tuple[float, float]:
    """(input tokens, output tokens) actually implied by the calls the pipeline made for this case, including a repair call."""
    inputs = outputs = 0.
    text = case['source']
    for index in range(provider.coach_calls):
        reminder = len(REPAIR_REMINDER) + len('formatting_reminder') if index else 0
        inputs += tokens(len(COACH_PROMPT) + len(json.dumps({'learner_text': text})) + reminder + SCHEMA_CHARS['coach']) + 30
        outputs += tokens(len(json.dumps(provider.returned[index]))) if index < len(provider.returned) else 0
    if provider.check_calls:
        improved = provider.returned[-1]['improved_text']
        inputs += tokens(len(MEANING_PROMPT) + len(json.dumps({'original': text, 'improved': improved})) + SCHEMA_CHARS['check']) + 30
        outputs += 6
    return inputs, outputs


def usd(inputs, outputs):
    return inputs * PRICE_IN / 1e6 + outputs * PRICE_OUT / 1e6


async def run_case(case, lenient):
    transcription = {'text': case.get('recognized_text') or case['source'], 'confidence': .93, 'uncertainty': [], 'source': 'openai',
                     **case.get('transcription', {})}
    text, reason = eligibility(case['source'], transcription, case['source'], MIN_CONFIDENCE)
    provider = ScriptedProvider(case, lenient)
    started = time.perf_counter()
    if text is None:
        outcome, code = 'unavailable', reason
    else:
        try:
            decision = await coach(provider, text)
            outcome = ('accepted_unchanged' if decision.status == 'ready' and decision.improved_text == text.strip() else
                       'accepted' if decision.status == 'ready' else 'rejected')
            code = decision.rejection
        except Exception as error:  # The API records any provider error as a failed claim.
            outcome, code = 'failed', type(error).__name__
    elapsed = (time.perf_counter() - started) * 1000
    inputs, outputs = estimate(case, provider, outcome)
    leaked = [secret for secret in (case.get('passage_fact'),) if secret and secret in json.dumps(provider.sent)]
    return {'id': case['id'], 'category': case['category'], 'label': case['label'], 'expect': case['expect'], 'outcome': outcome,
            'code': code, 'layer': LAYERS.get(code) if outcome == 'rejected' else None, 'coach_calls': provider.coach_calls,
            'check_calls': provider.check_calls, 'repaired': provider.coach_calls == 2, 'local_ms': round(elapsed, 3), 'input_tokens': round(inputs), 'output_tokens': round(outputs),
            'usd': usd(inputs, outputs), 'passage_leaked': bool(leaked)}


def quantiles(values):
    ordered = sorted(values)
    if not ordered:
        return {'p50': None, 'p95': None, 'max': None}
    return {'p50': round(statistics.median(ordered), 3), 'p95': round(ordered[min(len(ordered) - 1, math.ceil(.95 * len(ordered)) - 1)], 3), 'max': round(ordered[-1], 3)}


def summarize(results):
    by = defaultdict(list)
    for r in results:
        by[r['label']].append(r)
    legit = by['legit'] + by['comprehension_preserved']
    accepted_legit = [r for r in legit if r['outcome'] == 'accepted']
    changing = by['meaning_changing'] + by['comprehension_fixed']
    false_accepts = [r['id'] for r in changing if r['outcome'] in ('accepted', 'accepted_unchanged')]
    rejected_layers = Counter(r['layer'] for r in changing if r['outcome'] == 'rejected')
    called = [r for r in results if r['coach_calls']]
    costs = [r['usd'] for r in called]
    mean_cost = statistics.mean(costs) if costs else 0
    return {
        'cases': len(results),
        'by_label': {label: len(rows) for label, rows in sorted(by.items())},
        'legitimate_corrections': {'total': len(legit), 'accepted': len(accepted_legit),
            'acceptance_rate': round(len(accepted_legit) / len(legit), 3) if legit else None,
            'false_rejections': [{'id': r['id'], 'code': r['code'], 'layer': r['layer']} for r in legit if r['outcome'] != 'accepted']},
        'natural_answers': {'total': len(by['natural']), 'acknowledged_unchanged': sum(r['outcome'] == 'accepted_unchanged' for r in by['natural'])},
        'meaning_changing_candidates': {'total': len(changing), 'accepted_FALSE_ACCEPTS': false_accepts,
            'false_accept_rate': round(len(false_accepts) / len(changing), 3) if changing else None,
            'rejected_by_layer': dict(sorted(rejected_layers.items(), key=lambda item: str(item[0])))},
        'comprehension_errors': {'preserved_and_accepted': sum(r['outcome'] == 'accepted' for r in by['comprehension_preserved']),
            'preserved_total': len(by['comprehension_preserved']), 'fixed_candidates_accepted': sum(r['outcome'] == 'accepted' for r in by['comprehension_fixed'])},
        'ineligible_inputs': {'total': len(by['ineligible']), 'blocked_before_any_provider_call': sum(r['outcome'] == 'unavailable' and not r['coach_calls'] for r in by['ineligible'])},
        'provider_errors': {'total': len(by['provider_error']), 'recorded_as_failed': sum(r['outcome'] == 'failed' for r in by['provider_error'])},
        'automatic_repair': {'used': sum(r['repaired'] for r in results), 'recovered': sum(r['repaired'] and r['outcome'] == 'accepted' for r in results),
            'max_coach_calls_per_case': max((r['coach_calls'] for r in results), default=0)},
        'privacy': {'passage_facts_sent_to_provider': sum(r['passage_leaked'] for r in results)},
        'mismatches_vs_expectation': [{'id': r['id'], 'expected': r['expect'], 'got': r['outcome'], 'code': r['code']} for r in results
            if {'accept': 'accepted', 'accept_unchanged': 'accepted_unchanged', 'reject': 'rejected'}.get(r['expect'], r['expect']) != r['outcome']],
        'local_pipeline_latency_ms': quantiles([r['local_ms'] for r in results]),
        'estimated_cost': {'basis': 'chars/4 heuristic on the real prompts, schemas and scripted outputs, +30 framing tokens per request; '
                           f'{MODEL} at ${PRICE_IN}/M input and ${PRICE_OUT}/M output (standard tier, OpenAI pricing page 2026-10-22); no cached-input discount',
            'requests_that_reached_a_provider': len(called), 'provider_calls': sum(r['coach_calls'] + r['check_calls'] for r in results),
            'mean_usd_per_attempt': round(mean_cost, 6), 'p95_usd_per_attempt': round(sorted(costs)[min(len(costs) - 1, math.ceil(.95 * len(costs)) - 1)], 6) if costs else 0,
            'usd_per_1000_attempts': round(mean_cost * 1000, 2),
            'worst_case_usd_per_attempt_with_sdk_retries': round(max(costs, default=0) * (1 + SDK_RETRIES), 6),
            'max_usd_per_learner_per_day_at_cap_10': round(max(costs, default=0) * 10, 6)},
    }


async def evaluate(cases):
    scripted = [await run_case(case, lenient=False) for case in cases]
    lenient = [await run_case(case, lenient=True) for case in cases]
    layer4_only = sorted(r['id'] for r, s in zip(lenient, scripted)
                         if s['label'] in ('meaning_changing', 'comprehension_fixed') and s['outcome'] == 'rejected' and s['code'] == 'meaning_check_failed'
                         and r['outcome'] in ('accepted', 'accepted_unchanged'))
    return {'harness': 'mock', 'provider': 'scripted (no network, no credentials)', 'prompt_version': PROMPT_VERSION,
            'fixtures_sha256': sha256(FIXTURES.read_bytes()).hexdigest(),
            'scripted_verifier': summarize(scripted),
            'lenient_verifier_worst_case': {'note': 'The independent comparison always answers "same meaning": what layers 1-3 alone catch.',
                **{k: v for k, v in summarize(lenient).items() if k in ('meaning_changing_candidates', 'legitimate_corrections')}},
            'caught_only_by_layer_4': layer4_only,
            'per_case': scripted}


def plan(cases, repeats, budget):
    """Reservation arithmetic for a FUTURE owner-approved live run. Loads no credentials and makes no network call."""
    eligible = [c for c in cases if c['label'] not in ('ineligible',)]
    coach_in = [tokens(len(COACH_PROMPT) + len(json.dumps({'learner_text': c['source']})) + SCHEMA_CHARS['coach']) + 30 for c in eligible]
    check_in = [tokens(len(MEANING_PROMPT) + len(json.dumps({'original': c['source'], 'improved': (c['candidate'] or {}).get('improved_text', c['source'])})) + SCHEMA_CHARS['check']) + 30 for c in eligible]
    max_out = 700  # Coach output ceiling reserved per request (schema allows up to ~2,400 characters plus notes).
    requests = len(eligible) * repeats * 2            # every case may need both calls
    reserve_in = (sum(coach_in) + sum(check_in)) * repeats * 1.3
    reserve_out = requests * max_out
    reserved = usd(reserve_in, reserve_out)
    return {'harness': 'plan', 'credentials_loaded': False, 'network_calls': 0, 'executes_nothing': True,
            'status': 'PROPOSAL ONLY. Approval needed from the owner before any run; this repository has no paid-execution mode.',
            'model': f'{MODEL} (pin an exact snapshot before approval; account access unverified)', 'prompt_version': PROMPT_VERSION,
            'fixtures': len(cases), 'cases_eligible_for_a_provider': len(eligible), 'repeats': repeats,
            'max_requests': requests, 'concurrency': 1, 'sdk_max_retries': 0, 'timeout_seconds': 30,
            'note_on_retries': 'Production OpenAIProvider uses max_retries=2; a live harness must build its own client with max_retries=0.',
            'reserved_input_tokens': round(reserve_in), 'reserved_output_tokens': reserve_out, 'reservation_usd': round(reserved, 4),
            'proposed_hard_budget_usd': budget, 'within_budget': reserved <= budget,
            'rules': ['synthetic fixtures only; no production learner data', 'log intent durably before each request; never resend after an ambiguous failure',
                      'stop on budget, price, model, prompt or fixture-hash change', 'report real usage and latency (p50/p95) per call and per improvement',
                      'a model that changes meaning is a finding, not a retry: record it'],
            'fixtures_sha256': sha256(FIXTURES.read_bytes()).hexdigest()}


def render(report) -> str:
    s = report['scripted_verifier']
    legit, changing, cost = s['legitimate_corrections'], s['meaning_changing_candidates'], s['estimated_cost']
    lines = [f"cases {s['cases']} ({', '.join(f'{k} {v}' for k, v in s['by_label'].items())})",
             f"legitimate corrections accepted {legit['accepted']}/{legit['total']} ({legit['acceptance_rate']:.0%}); false rejections: " +
             (', '.join(f"{r['id']}[{r['code']}]" for r in legit['false_rejections']) or 'none'),
             f"natural answers acknowledged unchanged {s['natural_answers']['acknowledged_unchanged']}/{s['natural_answers']['total']}",
             f"meaning-changing candidates accepted {len(changing['accepted_FALSE_ACCEPTS'])}/{changing['total']}; rejected by layer {changing['rejected_by_layer']}",
             f"without layer 4 (lenient verifier) accepted {len(report['lenient_verifier_worst_case']['meaning_changing_candidates']['accepted_FALSE_ACCEPTS'])}: "
             + ', '.join(report['lenient_verifier_worst_case']['meaning_changing_candidates']['accepted_FALSE_ACCEPTS']),
             f"ineligible blocked before any provider call {s['ineligible_inputs']['blocked_before_any_provider_call']}/{s['ineligible_inputs']['total']}; "
             f"provider errors recorded as failed {s['provider_errors']['recorded_as_failed']}/{s['provider_errors']['total']}",
             f"automatic repair used {s['automatic_repair']['used']}, recovered {s['automatic_repair']['recovered']} (max coach calls per case {s['automatic_repair']['max_coach_calls_per_case']})",
             f"passage facts sent to provider {s['privacy']['passage_facts_sent_to_provider']}; mismatches vs expectation: {len(s['mismatches_vs_expectation'])}",
             f"local pipeline latency ms {s['local_pipeline_latency_ms']} (provider latency NOT measured in mock mode)",
             f"estimated cost per attempt ${cost['mean_usd_per_attempt']} (p95 ${cost['p95_usd_per_attempt']}); ${cost['usd_per_1000_attempts']}/1000 attempts"]
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    mock = sub.add_parser('mock'); mock.add_argument('--out')
    planner = sub.add_parser('plan'); planner.add_argument('--repeats', type=int, default=3); planner.add_argument('--budget-usd', type=float, default=1.0); planner.add_argument('--out')
    args = parser.parse_args(argv)
    cases = load_cases()
    if args.command == 'mock':
        report = asyncio.run(evaluate(cases))
        text = render(report)
    else:
        report = plan(cases, args.repeats, args.budget_usd)
        text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
