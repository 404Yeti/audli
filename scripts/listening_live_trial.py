"""Live validation of the Audli 2.0 OpenAI models. DOES NOT RUN WITHOUT AN EXACT, SIGNED-OFF APPROVAL FILE.

    .venv/bin/python -m scripts.listening_live_trial                      # prints the plan and worst-case cost; makes no call
    .venv/bin/python -m scripts.listening_live_trial --make-audio DIR     # builds the free local audio set (espeak-ng + ffmpeg); no call
    OPENAI_API_KEY=... .venv/bin/python -m scripts.listening_live_trial --run --approval approval.json [--audio-dir DIR]

`--run` first does a compatibility pre-flight: one free `models.retrieve` per model id and one real call per matrix row. If any model, endpoint
or parameter is refused (auth, unknown model, bad request) it stops there, writes a report and exits with status 2, so a wrong configuration
costs cents, not a matrix of identical failures. Spend is cumulative across runs: earlier reports in the results folder count against the
approved ceiling.

The approval file must name the sha256 of docs/listening-live-matrix.json + docs/listening-live-cases.json (so it approves exactly what will
run), a spend ceiling no higher than the matrix cap (US$5), who approved, and when it expires. Before every call the harness reserves the
call's worst-case cost and stops at the ceiling. It writes a results file with verdicts, counts and costs only: never audio, never the key.
It uses the production adapters (app/listening/providers.py), so a pass here validates the code path that will run in the worker.
"""
import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.listening.domain import LessonContent, check_level_fit, ground_assessment, normalize_text
from app.listening.models import AUDIO_PRICES, TEXT_PRICES, AssessmentModel, TranscriptionModel, audio_cost, text_cost
from app.listening.providers import (AssessmentInput, OpenAIAssessor, OpenAITranscriber, ProviderPermanent, ProviderTemporary, make_client,
                                     translate_error)
from app.listening.seed import FIXTURES, LESSONS

ROOT = Path(__file__).resolve().parents[1]
MATRIX, CASES = ROOT / 'docs' / 'listening-live-matrix.json', ROOT / 'docs' / 'listening-live-cases.json'
HARD_CAP_USD = 5.0
SAFETY = 1.5          # worst-case multiplier on every estimate


class BudgetExceeded(Exception):
    pass


def digest() -> str:
    return hashlib.sha256(MATRIX.read_bytes() + b'\n' + CASES.read_bytes()).hexdigest()


def load() -> tuple[dict, dict]:
    return json.loads(MATRIX.read_text()), json.loads(CASES.read_text())


def row_calls(row: dict, cases: dict) -> int:
    if row['task'] == 'assess':
        return len(cases['assessment']) * row['repeats']
    if row['task'] == 'transcribe':
        return row['clips'] * row['repeats']
    return len(cases['generation']) * row['repeats']


def row_cost(row: dict, cases: dict) -> float:
    """Worst-case USD for the row (estimates x SAFETY)."""
    calls = row_calls(row, cases)
    if row['task'] == 'transcribe':
        return SAFETY * calls * audio_cost(row['model'], row['avg_seconds'])
    return SAFETY * calls * text_cost(row['model'], row['est_in'], row['est_out'])


def plan(matrix: dict, cases: dict) -> tuple[list[dict], float]:
    rows = [{'id': r['id'], 'task': r['task'], 'model': r['model'], 'calls': row_calls(r, cases), 'worst_case_usd': round(row_cost(r, cases), 4), 'purpose': r['purpose']}
            for r in matrix['rows']]
    return rows, round(sum(r['worst_case_usd'] for r in rows), 4)


def prior_spend(directory: Path) -> float:
    """Estimated USD already spent by earlier runs under the same approval (their reports are kept in the results folder)."""
    total = 0.0
    for report in sorted(directory.glob('*.json')) if directory.exists() else []:
        try:
            total += float(json.loads(report.read_text()).get('spent_usd_estimate', 0))
        except (ValueError, OSError):
            continue
    return total


def check_approval(path: str | None, total: float, now: datetime | None = None, already_spent: float = 0.0) -> float:
    """Returns the approved ceiling in USD, or exits. Nothing is spent unless this passes."""
    if not path:
        raise SystemExit('Refusing to run: --approval FILE is required. Print the plan first (no flags) and have it approved.')
    approval = json.loads(Path(path).read_text())
    now = now or datetime.now(timezone.utc)
    for field in ('matrix_sha256', 'max_usd', 'approved_by', 'approved_at', 'expires_at'):
        if not approval.get(field):
            raise SystemExit(f'Refusing to run: approval is missing "{field}".')
    if approval['matrix_sha256'] != digest():
        raise SystemExit('Refusing to run: the approval is for a different matrix or case file than the one on disk.')
    ceiling = float(approval['max_usd'])
    if not 0 < ceiling <= min(HARD_CAP_USD, load()[0]['cap_usd']):
        raise SystemExit(f'Refusing to run: max_usd must be above 0 and at most {HARD_CAP_USD}.')
    if datetime.fromisoformat(approval['expires_at']) < now:
        raise SystemExit('Refusing to run: the approval has expired.')
    if total > ceiling - already_spent:
        raise SystemExit(f'Refusing to run: the worst-case cost {total} is above what is left of the approved ceiling ({ceiling} - {already_spent:.4f} already spent). Shrink the matrix or raise the approval.')
    return ceiling


class Ledger:
    """Tracks spend. Reserves each call's worst case first, then replaces it with the provider-reported usage when there is one."""
    def __init__(self, ceiling: float, already_spent: float = 0.0):
        self.ceiling, self.spent, self.calls = ceiling, already_spent, 0

    def reserve(self, worst_case: float) -> None:
        if self.spent + worst_case > self.ceiling:
            raise BudgetExceeded(f'next call could exceed the ceiling ({self.spent + worst_case:.4f} > {self.ceiling})')
        self.spent += worst_case
        self.calls += 1

    def settle(self, worst_case: float, actual: float | None) -> None:
        if actual is not None:
            self.spent += actual - worst_case


class Usage:
    """Reads token/second usage from every JSON response the SDK receives (httpx hook), so cost is measured, not guessed."""
    def __init__(self):
        self.last: dict | None = None

    def hook(self, response: httpx.Response) -> None:
        response.read()
        try:
            self.last = response.json().get('usage')
        except ValueError:
            self.last = None

    def cost(self, model: str) -> float | None:
        u = self.last or {}
        if 'input_tokens' in u or 'prompt_tokens' in u:
            return text_cost(model, u.get('input_tokens', u.get('prompt_tokens', 0)), u.get('output_tokens', u.get('completion_tokens', 0))) if model in TEXT_PRICES else None
        if u.get('type') == 'duration':
            return audio_cost(model, u['seconds']) if model in AUDIO_PRICES else None
        return None


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)] if ordered else 0.0


def fixture_points(lesson: str) -> tuple[AssessmentInput, list[str]]:
    content = LessonContent.model_validate_json((FIXTURES / lesson / 'lesson.json').read_text())
    points = [p.model_dump() for p in content.spoken.points]
    return AssessmentInput(content.title, [s.text for s in content.sentences], content.spoken.prompt, points, ''), [p['id'] for p in points]


def run_assessment(row: dict, cases: dict, ledger: Ledger, make_http) -> dict:
    usage = Usage()
    judge = OpenAIAssessor(make_client(os.environ['OPENAI_API_KEY'], 45, make_http(usage)),
                           AssessmentModel(row['model'], row['api'], row['reasoning'], 45))
    worst = SAFETY * text_cost(row['model'], row['est_in'], row['est_out'])
    results, malformed, ungrounded, latencies, false_covered, signatures = [], 0, 0, [], 0, {}
    for case in cases['assessment']:
        base, ids = fixture_points(case['lesson'])
        request = AssessmentInput(base.title, base.script, base.question, base.points, case['transcript'])
        for repeat in range(row['repeats']):
            ledger.reserve(worst)
            started = time.monotonic()
            try:
                verdicts = judge.assess(request)
                latencies.append(time.monotonic() - started)
                grounded = ground_assessment(verdicts, ids, case['transcript'])
                ungrounded += sum(1 for raw, kept in zip(verdicts, grounded) if raw.get('state') in ('covered', 'partly') and kept['state'] == 'unclear')
                states = {v['id']: v['state'] for v in grounded}
                ok = all(states[i] in case['allowed'][i] for i in ids)
                false_covered += sum(1 for i in ids if states[i] == 'covered' and 'covered' not in case['allowed'][i])
                signatures.setdefault(case['id'], set()).add(tuple(sorted(states.items())))
            except (ValueError, ProviderTemporary, ProviderPermanent) as error:
                malformed += isinstance(error, ValueError)
                ok, states = False, {'error': getattr(error, 'code', 'unusable')}
                if isinstance(error, ProviderPermanent) and error.code != 'provider_refused':
                    ledger.settle(worst, 0.0)
                    return {'incompatible': error.code, 'case': case['id'], 'passed': 0, 'of': 0}      # a configuration the provider refuses is not retried
            ledger.settle(worst, usage.cost(row['model']))
            results.append({'case': case['id'], 'category': case['category'], 'repeat': repeat, 'ok': ok, 'states': states})
    by_category = {}
    for r in results:
        by_category.setdefault(r['category'], []).append(r['ok'])
    return {'passed': sum(r['ok'] for r in results), 'of': len(results), 'by_category': {k: f'{sum(v)}/{len(v)}' for k, v in by_category.items()},
            'malformed': malformed, 'ungrounded_claims_downgraded': ungrounded, 'false_covered': false_covered,
            'inconsistent_cases': sum(1 for s in signatures.values() if len(s) > 1) if row['repeats'] > 1 else None,
            'latency_p50': round(statistics.median(latencies), 2) if latencies else None, 'latency_p95': round(percentile(latencies, .95), 2) if latencies else None,
            'results': results}


def run_transcription(row: dict, audio_dir: Path | None, ledger: Ledger, make_http) -> dict:
    if audio_dir is None or not (audio_dir / 'manifest.json').exists():
        return {'skipped': 'no --audio-dir with manifest.json (build one with --make-audio)'}
    usage = Usage()
    heard = OpenAITranscriber(make_client(os.environ['OPENAI_API_KEY'], 45, make_http(usage)), TranscriptionModel(row['model'], 45))
    manifest = json.loads((audio_dir / 'manifest.json').read_text())[:row['clips']]
    worst = SAFETY * audio_cost(row['model'], row['avg_seconds'])
    results = []
    for clip in manifest:
        ledger.reserve(worst)
        try:
            out = heard.transcribe((audio_dir / clip['file']).read_bytes(), 'audio/wav')
            words = normalize_text(out.text).split()
            if clip['kind'] == 'speech':
                keys = clip['keywords']
                ok = sum(1 for k in keys if k in words) >= 0.8 * len(keys)
            else:
                ok = (not out.reliable) or len(words) < 3
            results.append({'clip': clip['file'], 'kind': clip['kind'], 'ok': ok, 'reliable': out.reliable, 'words': len(words)})
        except (ValueError, ProviderTemporary, ProviderPermanent) as error:
            if isinstance(error, ProviderPermanent) and error.code != 'provider_refused':
                ledger.settle(worst, 0.0)
                return {'incompatible': error.code, 'clip': clip['file'], 'passed': 0, 'of': 0}
            results.append({'clip': clip['file'], 'kind': clip['kind'], 'ok': False, 'error': getattr(error, 'code', 'unusable')})
        ledger.settle(worst, usage.cost(row['model']))
    return {'passed': sum(r['ok'] for r in results), 'of': len(results), 'results': results}


def run_generation(row: dict, cases: dict, ledger: Ledger, make_http) -> dict:
    usage = Usage()
    client = make_client(os.environ['OPENAI_API_KEY'], 90, make_http(usage))
    example = (FIXTURES / 'pharmacy_a2' / 'lesson.json').read_text()
    worst = SAFETY * text_cost(row['model'], row['est_in'], row['est_out'])
    results = []
    for case in cases['generation']:
        for repeat in range(row['repeats']):
            ledger.reserve(worst)
            try:
                response = client.responses.create(model=row['model'], store=False, max_output_tokens=8000, text={'format': {'type': 'json_object'}},
                    instructions='Write ONE original English listening lesson as JSON in exactly the shape of the example (same keys, same kinds of content, same id rules). '
                                 'Two speakers, 12 to 16 short sentences, a multiple choice question with one correct option, a spoken question with 3 key points, '
                                 'a fill-in-the-blank with one ___ gap, and a 4-item sequencing question whose first listed order is not the correct one. '
                                 'Return JSON only. Example:\n' + example,
                    input=f"CEFR level {case['level']}. Topic: {case['topic']}.")
                lesson = LessonContent.model_validate_json(response.output_text)
                problems = check_level_fit(lesson)
                results.append({'case': case['id'], 'repeat': repeat, 'valid': True, 'level_fit': not problems, 'problems': problems})
            except (ValidationError, ValueError) as error:
                results.append({'case': case['id'], 'repeat': repeat, 'valid': False, 'level_fit': False, 'problems': [type(error).__name__]})
            except Exception as raw:
                error = translate_error(raw)
                if not isinstance(error, (ProviderTemporary, ProviderPermanent)):
                    raise
                if isinstance(error, ProviderPermanent):
                    ledger.settle(worst, 0.0)
                    return {'incompatible': error.code, 'case': case['id'], 'valid': 0, 'of': 0}
                results.append({'case': case['id'], 'repeat': repeat, 'valid': False, 'level_fit': False, 'problems': [error.code]})
            ledger.settle(worst, usage.cost(row['model']))
    return {'valid': sum(r['valid'] for r in results), 'level_fit': sum(r['level_fit'] for r in results), 'of': len(results), 'results': results}


def preflight(matrix: dict, cases: dict, audio_dir: Path | None, ledger: Ledger, make_http) -> dict:
    """Free model lookups, then one real call per row. Anything the provider refuses as a configuration problem ends the run here."""
    usage = Usage()
    client = make_client(os.environ['OPENAI_API_KEY'], 30, make_http(usage))
    found = {}
    for model in dict.fromkeys(r['model'] for r in matrix['rows']):
        try:
            client.models.retrieve(model)                    # no tokens, no charge
            found[model] = 'available'
        except Exception as raw:
            error = translate_error(raw)
            found[model] = getattr(error, 'code', 'unreachable')
    one = {'assessment': cases['assessment'][:1], 'generation': cases['generation'][:1]}
    rows = {}
    for row in matrix['rows']:
        if found[row['model']] in ('provider_auth', 'provider_model'):
            rows[row['id']] = {'incompatible': found[row['model']], 'skipped': 'model lookup refused'}
            continue
        small = {**row, 'repeats': 1, 'clips': 1}
        result = {'assess': lambda: run_assessment(small, one, ledger, make_http), 'transcribe': lambda: run_transcription(small, audio_dir, ledger, make_http),
                  'generate': lambda: run_generation(small, one, ledger, make_http)}[row['task']]()
        rows[row['id']] = {k: v for k, v in result.items() if k != 'results'}
    return {'models': found, 'rows': rows, 'ok': not any('incompatible' in r for r in rows.values()) and all(v == 'available' for v in found.values())}


def make_audio(directory: Path) -> None:
    """Free, local, repeatable: espeak-ng speech for some assessment cases, plus noise, silence and a tone made by ffmpeg. Add human clips by hand."""
    from imageio_ffmpeg import get_ffmpeg_exe
    directory.mkdir(parents=True, exist_ok=True)
    manifest, ffmpeg = [], get_ffmpeg_exe()
    _, cases = load()
    for case in [c for c in cases['assessment'] if c['category'] in ('correct', 'incorrect')][:14]:
        target = directory / f"{case['id']}.wav"
        subprocess.run([os.environ.get('ESPEAK_NG', 'espeak-ng'), '-v', 'en-us', '-s', '150', '-w', str(target), '--', case['transcript']], check=True, capture_output=True)
        keys = sorted({w for w in normalize_text(case['transcript']).split() if len(w) >= 6})[:6]
        manifest.append({'file': target.name, 'kind': 'speech', 'keywords': keys})
    for name, source in (('noise-white', 'anoisesrc=d=8:c=white:a=0.2'), ('noise-pink', 'anoisesrc=d=8:c=pink:a=0.2'), ('silence', 'anullsrc=r=16000:cl=mono'),
                         ('tone', 'sine=f=440:d=8')):
        target = directory / f'{name}.wav'
        extra = ['-t', '8'] if name == 'silence' else []
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', source, *extra, '-ar', '16000', '-ac', '1', str(target)], check=True)
        manifest.append({'file': target.name, 'kind': 'noise'})
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(f'{len(manifest)} clips written to {directory} (no call made)')


def main(argv: list[str] | None = None, http_factory=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--approval')
    parser.add_argument('--audio-dir', type=Path)
    parser.add_argument('--make-audio', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'docs' / 'listening-live-results')
    args = parser.parse_args(argv)
    if args.make_audio:
        make_audio(args.make_audio)
        return 0
    matrix, cases = load()
    rows, total = plan(matrix, cases)
    if not args.run:
        print(f'matrix sha256 {digest()}\ncap US${matrix["cap_usd"]}  worst-case total US${total}  (estimates x{SAFETY}; no call made)')
        for r in rows:
            print(f"  {r['id']:3} {r['task']:10} {r['model']:24} calls={r['calls']:4} worst-case=US${r['worst_case_usd']:.4f}  {r['purpose']}")
        return 0
    already = prior_spend(args.out)
    ceiling = check_approval(args.approval, total, already_spent=already)
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('Refusing to run: OPENAI_API_KEY is not set in the environment.')
    ledger = Ledger(ceiling, already)
    make_http = http_factory or (lambda usage: httpx.Client(event_hooks={'response': [usage.hook]}))
    report = {'matrix_sha256': digest(), 'started_at': datetime.now(timezone.utc).isoformat(), 'ceiling_usd': ceiling, 'rows': {}}
    try:
        report['preflight'] = preflight(matrix, cases, args.audio_dir, ledger, make_http)
        if not report['preflight']['ok']:
            report.update(stopped='pre-flight found an incompatible model or parameter; the full matrix was NOT run', calls=ledger.calls,
                          spent_usd_estimate=round(ledger.spent - already, 4))
            args.out.mkdir(parents=True, exist_ok=True)
            target = args.out / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
            target.write_text(json.dumps(report, indent=1))
            print(f'PRE-FLIGHT FAILED ({ledger.calls} calls, about US${ledger.spent - already:.4f}); see {target}')
            return 2
        for row in matrix['rows']:
            runner = {'assess': lambda: run_assessment(row, cases, ledger, make_http), 'transcribe': lambda: run_transcription(row, args.audio_dir, ledger, make_http),
                      'generate': lambda: run_generation(row, cases, ledger, make_http)}[row['task']]
            report['rows'][row['id']] = runner()
    except BudgetExceeded as stop:
        report['stopped'] = str(stop)
    report.update(calls=ledger.calls, spent_usd_estimate=round(ledger.spent - already, 4))
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    target.write_text(json.dumps(report, indent=1))
    print(f'{ledger.calls} calls, about US${ledger.spent - already:.4f} spent this run (US${ledger.spent:.4f} of US${ceiling} in total); results in {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
