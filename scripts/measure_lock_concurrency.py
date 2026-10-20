"""Print the two-learner lock measurement as JSON (AUD-68 Phase 0). Fake providers, fixed delays; SQLite.

    .venv/bin/python scripts/measure_lock_concurrency.py [--delays 0.3 1.0] [--runs 3] > out.json
"""
import argparse, asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
tmp = tempfile.TemporaryDirectory(prefix='audli-lock-bootstrap-')
os.environ.update({'AUDLI_ENVIRONMENT': 'development', 'AUDLI_PERSISTENCE': 'sqlite', 'AUDLI_DATABASE_URL': '',
                   'AUDLI_LEARNER_ID': '', 'AUDLI_ALLOWED_ORIGINS': '', 'AUDLI_DATA_DIR': tmp.name, 'AUDLI_AUTH_MODE': 'local'})
from app.storage.adapters import ProgressRepository  # noqa: E402
from lock_concurrency import measure  # noqa: E402


async def main(delays, runs, per_learner):
    results = []
    for delay in delays:
        for run in range(runs):
            with tempfile.TemporaryDirectory(prefix='audli-lock-run-') as directory:
                result = await measure(ProgressRepository(Path(directory) / 'audli.sqlite3', None), delay, per_learner)
            results.append({'run': run + 1, **{k: v for k, v in result.items() if k not in ('turn_upload_lock_wait', 'turn_upload_lock_hold')}})
    return results

parser = argparse.ArgumentParser()
parser.add_argument('--delays', type=float, nargs='+', default=[0.3, 1.0])
parser.add_argument('--runs', type=int, default=3)
parser.add_argument('--per-learner', action='store_true', help='Enable AUDLI_PER_LEARNER_LOCKS for the run (AUD-70).')
args = parser.parse_args()
print(json.dumps({'harness': 'fake providers; fixed delay; SQLite; ASGI in-process', 'results': asyncio.run(main(args.delays, args.runs, args.per_learner))}, indent=1))
