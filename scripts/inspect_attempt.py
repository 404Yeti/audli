"""Local development inspection only; never attached to a learner HTTP endpoint."""
import argparse
import json
from app.config import Settings
from app.repository import ProgressRepository

parser=argparse.ArgumentParser()
parser.add_argument('attempt_id')
args=parser.parse_args()
repo=ProgressRepository(Settings().data_dir/'audli.sqlite3')
attempt=repo.attempt(args.attempt_id)
if not attempt or attempt['status']!='evaluated':
    raise SystemExit('Only completed evaluated attempts can be inspected.')
exercise=repo.exercise(attempt['exercise_id'])
print(json.dumps({'exercise':json.loads(exercise['content']),'difficulty':json.loads(exercise['difficulty']),
    'transcription':json.loads(attempt['transcription']),'confirmed_text':attempt['confirmed_text'],**repo.result(args.attempt_id)},indent=2))
