"""Offline replay of a saved live evaluation through the CURRENT validator. No network, no credential, no provider call.

The replay re-runs layers 1-3 (and the cosmetic-only rule) on the provider responses that were saved. It can NOT show the effect of
a changed coaching prompt or a changed independent-comparison (layer 4) prompt: those only change what a provider would say.
A saved layer-4 verdict is reused only when the same candidate reached layer 4 in the saved run; a candidate that the saved run
rejected earlier, and that now gets past layers 1-3, is reported as NEEDS_LIVE_CHECK, never as accepted.

    python scripts/replay_review_improvement_eval.py [--responses FILE] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from app.improvement import LanguageCoaching, is_unchanged, natural_already, reject_reason   # noqa: E402
import run_review_improvement_eval as runner   # noqa: E402

RESPONSES = ROOT / 'tests' / 'fixtures' / 'review_improvement_run1_responses.json'


def replay_record(case: dict, saved: dict) -> dict:
    """What the current layers 1-3 do with the saved final coaching response of one fixture."""
    out = {'id': case['id'], 'category': case['category'], 'saved_outcome': saved['outcome'], 'saved_classification': saved['classification'],
           'saved_layer': saved.get('layer'), 'saved_code': saved.get('code')}
    coaching_raw = (saved.get('raw') or {}).get('coach') or []
    if saved['outcome'] == 'unavailable' or not coaching_raw:
        return out | {'now_outcome': saved['outcome'], 'now_classification': saved['classification'], 'now_layer': None, 'now_code': saved.get('code'), 'needs_live_check': False}
    coaching = LanguageCoaching.model_validate(coaching_raw[-1])
    text = case['source'].strip()
    candidate = coaching.improved_text.strip()
    needs_live = False
    if natural_already(text, coaching):
        now_outcome, layer, code, candidate_scored = 'accepted_unchanged', None, None, text
    else:
        code = reject_reason(text, coaching)
        layer, candidate_scored = None, candidate
        if code is not None:
            now_outcome = 'rejected'
            from app.improvement import rejection_layer
            layer = rejection_layer(code)
        elif is_unchanged(text, candidate):
            now_outcome = 'accepted_unchanged'
        else:
            checks = (saved.get('raw') or {}).get('check') or []
            if checks:   # the same candidate was compared by the independent check in the saved run
                if checks[-1]['same_meaning']:
                    now_outcome = 'accepted'
                else:
                    now_outcome, layer, code = 'rejected', '4', 'meaning_check_failed'
            else:
                now_outcome, needs_live = 'needs_live_check', True
    scored = runner.score(case, candidate_scored)
    if needs_live:
        classification = 'needs_live_check'
    else:
        classification = runner.classify(case, now_outcome, scored, candidate_scored)
    return out | {'now_outcome': now_outcome, 'now_classification': classification, 'now_layer': layer, 'now_code': code, 'needs_live_check': needs_live}


def replay(responses_path: Path = RESPONSES) -> list[dict]:
    cases = {c['id']: c for c in runner.load_cases()}
    saved = json.loads(responses_path.read_text())['records']
    return [replay_record(cases[s['id']], s) for s in saved]


def compare(rows: list[dict]) -> str:
    changed = [r for r in rows if r['saved_classification'] != r['now_classification']]
    from collections import Counter
    before, after = Counter(r['saved_classification'] for r in rows), Counter(r['now_classification'] for r in rows)
    lines = ['# Offline replay of saved evaluation 1 through the current layers 1-3', '',
             '| classification | saved run | replay |', '|---|---|---|']
    for key in sorted(set(before) | set(after)):
        lines.append(f'| {key} | {before[key]} | {after[key]} |')
    lines += ['', '## Cases whose classification changed']
    for r in changed:
        lines.append(f"- {r['id']} ({r['category']}): {r['saved_classification']} [{r['saved_layer'] or '-'}/{r['saved_code'] or '-'}] -> {r['now_classification']} [{r['now_layer'] or '-'}/{r['now_code'] or '-'}]")
    lines += ['', 'FALSE_ACCEPT in replay: ' + (', '.join(r['id'] for r in rows if r['now_classification'] == 'FALSE_ACCEPT') or 'none'),
              'Needs a live check (cannot be settled offline): ' + (', '.join(r['id'] for r in rows if r['needs_live_check']) or 'none')]
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--responses', type=Path, default=RESPONSES)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    rows = replay(args.responses)
    print(json.dumps(rows, indent=1) if args.json else compare(rows))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
