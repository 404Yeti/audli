"""Build an offline AUD-20 review page from saved clips; never synthesize or approve."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def prepare(output: Path):
    rows = json.loads((output / 'results.json').read_text())['rows']
    if len(rows) != 12 or len({r['sample_id'] for r in rows}) != 12:
        raise ValueError('Expected twelve unique saved samples')
    for row in rows:
        if row['sample_id'] not in {f'P{i:02}' for i in range(1, 13)}:
            raise ValueError('Unexpected sample identifier')
        clip = output / (row['sample_id'] + '.mp3')
        if row['status'] != 'saved' or hashlib.sha256(clip.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Missing or changed audio; review source evidence first')
    ratings = list(csv.DictReader((output / 'ratings.csv').open()))
    payload = json.dumps({'rows': rows, 'ratings': ratings}).replace('<', '\\u003c')
    template = Path(__file__).with_name('accent_review.html').read_text()
    (output / 'pilot-review.html').write_text(template.replace('__REVIEW_DATA__', payload))
    print('Prepared pilot-review.html; audio, ratings.csv and approvals unchanged')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    prepare(parser.parse_args().output)
