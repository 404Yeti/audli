#!/usr/bin/env python3
"""Stage manifests for the migration 006 run (no secrets are ever written).

  manifest.py write FILE key=value ...      create/merge FILE (adds created_at)
  manifest.py get FILE key                  print one value
  manifest.py require RUN_DIR --migration-sha SHA --fingerprint FP --max-age-hours N STAGE [STAGE ...]
        succeeds only if every STAGE.json exists, has result=PASS, the same migration sha256 and target fingerprint
        (the rehearsal/restore stages record the PRODUCTION fingerprint they were taken from), and is not too old.
"""
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone


def load(path):
    return json.loads(pathlib.Path(path).read_text())


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    command = argv[1]
    if command == 'write':
        path = pathlib.Path(argv[2])
        data = load(path) if path.exists() else {}
        data.update(dict(item.split('=', 1) for item in argv[3:]))
        data['created_at'] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
        path.chmod(0o600)
    elif command == 'get':
        print(load(argv[2]).get(argv[3], ''))
    elif command == 'require':
        run_dir, args = pathlib.Path(argv[2]), argv[3:]
        options, stages = {}, []
        while args:
            if args[0].startswith('--'):
                options[args[0][2:]] = args[1]; args = args[2:]
            else:
                stages.append(args[0]); args = args[1:]
        oldest = datetime.now(timezone.utc) - timedelta(hours=float(options['max-age-hours']))
        problems = []
        for stage in stages:
            file = run_dir / f'{stage}.json'
            if not file.exists():
                problems.append(f'{stage}: no manifest at {file}'); continue
            data = load(file)
            if data.get('result') != 'PASS':
                problems.append(f'{stage}: result is {data.get("result")!r}, not PASS')
            if data.get('migration_sha256') not in (None, options['migration-sha']):
                problems.append(f'{stage}: recorded for a different migration file')
            if data.get('source_fingerprint') != options['fingerprint']:
                problems.append(f'{stage}: recorded for a different production database')
            if datetime.fromisoformat(data['created_at']) < oldest:
                problems.append(f'{stage}: older than {options["max-age-hours"]} h; repeat it')
        if problems:
            print('\n'.join(problems), file=sys.stderr)
            sys.exit(1)
    else:
        sys.exit(__doc__)


main(sys.argv)
