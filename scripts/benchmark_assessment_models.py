"""AUD-82: capped, paid comparison of evidence-assessment models. Synthetic ESL answers only.

* One fixed synthetic exercise (the demo passage); no learner data, accounts, database or Supabase.
* Uses the production prompt, schema and validation (OpenAIProvider.assess + validate_evidence); only the model changes.
* Gold labels are author-written and unreviewed: they rank candidates, they are not accuracy evidence.
* Estimated spend is tracked from reported token usage with a PRICES table; the run stops at --budget (default $1.50).
  Prices marked "assumed" are conservative guesses, not quotes. Real spend is checked in the OpenAI dashboard.
* Calls rotate across models per fixture so an early stop leaves balanced data. Latency is from this machine's network.
* Reads only OPENAI_API_KEY from --env-file; values are never printed.

    .venv/bin/python scripts/benchmark_assessment_models.py --env-file /path/.env --out docs/AUD-82-ASSESSMENT-MODEL-BENCHMARK.json
"""
import argparse, asyncio, json, os, statistics, sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# USD per 1M tokens (input, cached input, output). 'assumed' rows are deliberately high.
PRICES = {
    'gpt-4.1-mini': (0.40, 0.10, 1.60, 'published'),
    'gpt-4.1-nano': (0.10, 0.025, 0.40, 'published'),
    'gpt-4o-mini': (0.15, 0.075, 0.60, 'published'),
    'gpt-5-nano': (0.05, 0.005, 0.40, 'third-party'),
    'gpt-5-mini': (0.25, 0.025, 2.00, 'third-party'),
    'gpt-5.4-nano': (0.30, 0.03, 2.00, 'assumed'),
    'gpt-5.4-mini': (1.00, 0.10, 6.00, 'assumed'),
}
REASONING = {'gpt-5-nano': 'minimal', 'gpt-5-mini': 'minimal', 'gpt-5.4-nano': 'none', 'gpt-5.4-mini': 'none'}
BASELINE = 'gpt-4.1-mini'
MAX_OUTPUT_TOKENS = 3000
ORDER = ['main_idea_0', 'details_0', 'details_1', 'details_2', 'vocabulary_0', 'vocabulary_1', 'inference_0']
CODE = {'demonstrated': 'D', 'partially_demonstrated': 'P', 'misunderstood': 'M', 'insufficient_evidence': 'I'}

# gold: one letter per unit in ORDER. D demonstrated, M misunderstood, I insufficient evidence, ? not scored.
FIXTURES = [
    ('excellent', 'The team launched an app update but a database migration removed a required field, so customers could not save appointments. They returned to the old app version. Maya delayed the next release until Friday and plans to test the migration first so this will not happen again.', 'DDDDDDD'),
    ('main_idea_only', 'An app update failed and the team went back to the old version. They want to test more carefully to avoid another failure.', 'DIIIDID'),
    ('misunderstood', 'The app update worked well. The customers could save all their appointments. Maya decided to release it immediately on Monday. They did not need any more testing.', 'MIMMIMM'),
    ('nothing', 'I think someone was in an office. I do not know what happened.', 'IIIIIII'),
    ('broken_grammar_correct', 'Team update app Monday. Database change remove field app need. Customer cannot save appointment. Team go back old app, now work. Maya delay next release Friday. Test database copy first, no same problem again.', 'DDDDDDD'),
    ('vietnamese_pattern', 'Monday the team make new update for the app but have problem, the database change delete one field so customer no can save the appointment. Then they return the before version. Maya want wait until Friday and she test first.', 'DDDDDDD'),
    ('wrong_date', 'The update broke saving because the database lost a field. The team went back to the old version and Maya will release again on Wednesday after testing.', 'DDDMDDD'),
    ('short_fragments', 'App is broken. They fix. Friday.', '?IIIIII'.replace('?', 'P')),
    ('off_topic', 'My company also has a scheduling app. I like it very much but sometimes it is slow in the morning.', 'IIIIIII'),
    ('cause_only', 'Customers could not save appointments because something in the database was missing.', 'PDDIIII'),
    ('contradiction', 'The new update added a field to the database and customers loved it. Maya released the next version early.', 'MMMMIIM'),
    ('synonyms_no_keywords', 'Their software change caused trouble, since an important piece of stored information disappeared and people could not book times. The company reverted it, then pushed the next version to the end of the week, and will rehearse the change on a duplicate first.', 'DDDDDDD'),
]


def load_key(path: Path):
    for line in path.read_text().splitlines():
        name, sep, value = line.partition('=')
        if sep and name.strip() == 'OPENAI_API_KEY' and value.strip():
            os.environ['OPENAI_API_KEY'] = value.strip().strip('"\'')
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('OPENAI_API_KEY missing in env file.')


def pct(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 3) if values else None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--models', default=','.join(PRICES))
    parser.add_argument('--reps', type=int, default=2)
    parser.add_argument('--budget', type=float, default=1.50)
    args = parser.parse_args()
    load_key(args.env_file)
    from app.config import Settings
    from app.conversation import validate_evidence
    from app.models import ConversationTurn
    from app.services.demo import DemoProvider
    from app.services.openai_provider import OpenAIProvider

    exercise = await DemoProvider().generate(type('P', (), {'difficulty': type('D', (), {
        'duration_seconds': 60, 'vocabulary_level': 'B1', 'speech_rate': .9, 'information_density': 2})()})())
    spend = 0.0
    rows = {m: [] for m in args.models.split(',')}
    unsupported = {}

    class Bench(OpenAIProvider):
        model = BASELINE

        async def structured(self, schema, system, data):
            nonlocal spend
            from app.diagnostics import operation
            kwargs = {'reasoning': {'effort': REASONING[self.model]}} if self.model in REASONING else {}
            started = perf_counter()
            result = await self.client.responses.parse(model=self.model, store=False, max_output_tokens=MAX_OUTPUT_TOKENS,
                input=[{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(data)}],
                text_format=schema, **kwargs)
            self.last = {'seconds': perf_counter() - started, 'usage': result.usage}
            price = PRICES[self.model]
            usage = result.usage
            cached = getattr(getattr(usage, 'input_tokens_details', None), 'cached_tokens', 0) or 0
            spend += ((usage.input_tokens - cached) * price[0] + cached * price[1] + usage.output_tokens * price[2]) / 1e6
            if result.output_parsed is None:
                raise ValueError('refused or incomplete')
            return result.output_parsed

    provider = Bench(Settings(_env_file=None, openai_api_key=os.environ['OPENAI_API_KEY']))
    stopped = False
    for rep in range(args.reps):
        for name, text, gold in FIXTURES:
            for model in rows:
                if spend >= args.budget:
                    stopped = True
                    break
                if model in unsupported:
                    continue
                provider.model = model
                turns = [ConversationTurn(attempt_id=f'b-{rep}-{name}', text=text)]
                row = {'fixture': name, 'rep': rep, 'ok': False}
                try:
                    assessment = await provider.assess(exercise, turns)
                    validate_evidence(exercise, assessment, [text])
                    got = ''.join(CODE[next(u.status for u in assessment.units if f'{u.dimension}_{u.index}' == key)] for key in ORDER)
                    gold_letters = gold
                    scored = [(g, a) for g, a in zip(gold_letters, got) if g != '?']
                    positive = lambda c: c in 'DP'
                    row.update(ok=True, got=got, gold=gold_letters,
                        exact=sum(g == a or (positive(g) and positive(a)) for g, a in scored) / len(scored),
                        false_misunderstood=sum(positive(g) and a == 'M' for g, a in scored),
                        false_demonstrated=sum(g in 'IM' and positive(a) for g, a in scored),
                        output_tokens=provider.last['usage'].output_tokens, input_tokens=provider.last['usage'].input_tokens)
                except Exception as error:  # Record the type only; messages can contain payloads.
                    row['error'] = type(error).__name__
                    if type(error).__name__ in ('BadRequestError', 'NotFoundError', 'PermissionDeniedError') and not rows[model]:
                        unsupported[model] = type(error).__name__
                row['seconds'] = round(provider.last['seconds'], 3) if row.get('ok') else None
                rows[model].append(row)
            if stopped:
                break
        if stopped:
            break
    report = {'exercise': 'demo passage (synthetic)', 'fixtures': len(FIXTURES), 'reps_requested': args.reps,
              'estimated_spend_usd': round(spend, 4), 'budget_usd': args.budget, 'stopped_on_budget': stopped,
              'unsupported_models': unsupported, 'price_sources': {m: PRICES[m][3] for m in rows}, 'models': {},
              # Fixture names, unit-status letters and numbers only; no answer text.
              'rows': {m: [{k: r[k] for k in ('fixture', 'rep', 'ok', 'got', 'gold', 'seconds', 'output_tokens', 'input_tokens') if k in r} for r in v] for m, v in rows.items()}}
    for model, results in rows.items():
        good = [r for r in results if r['ok']]
        times = [r['seconds'] for r in good]
        report['models'][model] = {
            'calls': len(results), 'valid': len(good), 'invalid_or_error': len(results) - len(good),
            'errors': sorted({r['error'] for r in results if 'error' in r}),
            'latency_s': {'p50': pct(times, .5), 'p90': pct(times, .9), 'mean': round(statistics.mean(times), 3) if times else None},
            'mean_output_tokens': round(statistics.mean(r['output_tokens'] for r in good)) if good else None,
            'unit_agreement_with_gold': round(statistics.mean(r['exact'] for r in good), 3) if good else None,
            'false_misunderstood_units': sum(r['false_misunderstood'] for r in good),
            'false_demonstrated_units': sum(r['false_demonstrated'] for r in good),
        }
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


asyncio.run(main())
