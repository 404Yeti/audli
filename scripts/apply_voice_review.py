"""Apply the owner's voice-review decisions to the accent catalog: only what was explicitly approved or rejected, nothing else.

    python -m scripts.apply_voice_review decisions.txt              # dry run: prints the plan, changes nothing
    python -m scripts.apply_voice_review decisions.txt --write      # edits app/accent_catalog.py

`decisions.txt` is the text copied from docs/listening-accent-review/index.html ("Copy my decisions"), one line per accent:
    Irish: approve (Darragh (male) BOt7zZh6gzfWlIUYnyPz)         approve the proposal
    Irish: approve fNG1TqnY9mWgIFIExmZA                          approve an ALTERNATE (any listed candidate id on the line)
    Chinese: reject (Lee's voice (male) mBoVD3461U2BagYEwjeo)    reject that candidate (it is never approved or re-proposed)
    Irish: revoke                                                withdraw an EXISTING approval (the only way to change one; then approve again in a later round)
    Boston: unsure        /        Boston: no decision          leaves the accent exactly as it is
Partial approvals are normal: accents you do not mention, or mark unsure, stay disabled. A voice is approved ONLY by the word "approve"; successful synthesis never approves anything.
Nothing here spends money, calls a provider or touches production.
"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'app/accent_catalog.py'
NAMES = {'irish': 'Irish', 'canadian': 'Canadian', 'scottish': 'Scottish', 'indian': 'Indian', 'new_zealand': 'New Zealand', 'chinese': 'Chinese', 'japanese': 'Japanese',
         'filipino': 'Filipino', 'nigerian': 'Nigerian', 'southern': 'Southern US', 'new_york': 'New York', 'boston': 'Boston'}
BY_NAME = {v.lower(): k for k, v in NAMES.items()} | {'southern': 'southern', 'new zealand english': 'new_zealand'}
LINE = re.compile(r'^\s*(?P<label>[A-Za-z ]+?)\s*:\s*(?P<decision>approve|reject|revoke|unsure|no decision)\b(?P<rest>.*)$', re.IGNORECASE)
VOICE_ID = re.compile(r'\b[A-Za-z0-9]{20}\b')


class ReviewError(Exception):
    pass


def load_catalog(path: Path):
    spec = importlib.util.spec_from_file_location('accent_catalog_under_review', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse(text: str) -> list[dict]:
    """Decisions in the order given. Unknown accents and malformed decision lines are errors, never guesses."""
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = LINE.match(line)
        if not match:
            raise ReviewError(f'line {number}: cannot read "{line}"')
        accent = BY_NAME.get(match['label'].strip().lower())
        if accent is None:
            raise ReviewError(f'line {number}: unknown accent "{match["label"].strip()}"')
        out.append({'accent': accent, 'decision': match['decision'].lower(), 'ids': VOICE_ID.findall(match['rest']), 'line': number})
    return out


def plan(decisions: list[dict], catalog) -> list[dict]:
    """What would change, each with the current and proposed state. Raises ReviewError on anything ambiguous, invalid or conflicting; an unsure/undecided accent produces no change.
    An existing approval is never replaced: it must be revoked in its own explicit decision first. A rejected voice can never be approved. One voice never serves two accents."""
    changes, seen, chosen = [], set(), {}
    for d in decisions:
        accent, row = d['accent'], catalog.LISTENING_CANDIDATES[d['accent']]
        listed = [v for v, _, _ in row['second']]
        if accent in seen:
            raise ReviewError(f'{NAMES[accent]} appears twice; keep one line per accent')
        seen.add(accent)
        if d['decision'] in ('unsure', 'no decision'):
            continue
        named = [i for i in d['ids'] if i in listed]
        before = {'approved': row['approved'], 'rejected': list(row['rejected'])}
        if d['decision'] == 'revoke':
            if not row['approved']:
                raise ReviewError(f'{NAMES[accent]}: nothing is approved, so there is nothing to revoke')
            changes.append({'accent': accent, 'action': 'revoke', 'voice': row['approved'], 'before': before, 'after': {'approved': None, 'rejected': list(row['rejected'])}})
            continue
        if d['ids'] and not named:                                              # any id that is not a listed candidate (including speaker 1's) is an error, never ignored
            raise ReviewError(f'{NAMES[accent]}: {d["ids"]} is not a listed candidate; nothing was changed')
        if len(set(named)) > 1:
            raise ReviewError(f'{NAMES[accent]}: more than one candidate id on the {d["decision"]} line; name exactly one')
        if d['decision'] == 'approve':
            voice = named[0] if named else next((v for v in listed if v not in row['rejected']), None)
            if voice is None or voice in row['rejected']:
                raise ReviewError(f'{NAMES[accent]}: that voice was rejected and cannot be approved; choose another candidate')
            if voice == row['first']:
                raise ReviewError(f'{NAMES[accent]}: speaker 2 cannot be the speaker-1 voice')
            if row['approved'] == voice:
                continue                                                        # already approved: idempotent, nothing to do
            if row['approved']:
                raise ReviewError(f'{NAMES[accent]}: a different voice ({row["approved"]}) is already approved; write "{NAMES[accent]}: revoke" first, then approve in a later round')
            if voice in chosen:
                raise ReviewError(f'{NAMES[accent]}: that voice is already being approved for {NAMES[chosen[voice]]}; one voice cannot serve two accents')
            chosen[voice] = accent
            changes.append({'accent': accent, 'action': 'approve', 'voice': voice, 'before': before, 'after': {'approved': voice, 'rejected': list(row['rejected'])}})
        else:                                                                   # reject
            voice = named[0] if named else next((v for v in listed if v not in row['rejected'] and v != row['approved']), None)
            if voice is None:
                raise ReviewError(f'{NAMES[accent]}: nothing left to reject')
            if voice == row['approved']:
                raise ReviewError(f'{NAMES[accent]}: that voice is currently approved; write "{NAMES[accent]}: revoke" to withdraw it')
            if voice in row['rejected']:
                continue                                                        # already rejected: idempotent
            changes.append({'accent': accent, 'action': 'reject', 'voice': voice, 'before': before, 'after': {'approved': row['approved'], 'rejected': [*row['rejected'], voice]}})
    return changes


def apply(source: str, changes: list[dict]) -> str:
    """The catalog text with exactly those rows changed (header line of each accent's block); everything else byte-identical."""
    for c in changes:
        header = re.search(rf"^(    '{c['accent']}': \{{'first': '[A-Za-z0-9]{{20}}', 'approved': )(None|'[A-Za-z0-9]{{20}}')(, 'rejected': )\[([^\]]*)\](, 'second': \[)", source, re.M)
        if not header:
            raise ReviewError(f"cannot find the {c['accent']} row in the catalog")
        approved, rejected = header.group(2), [x.strip().strip("'") for x in header.group(4).split(',') if x.strip()]
        if c['action'] == 'approve':
            approved = f"'{c['voice']}'"
        elif c['action'] == 'revoke':
            approved = 'None'
        elif c['voice'] not in rejected:
            rejected.append(c['voice'])
            if approved == f"'{c['voice']}'":
                approved = 'None'
        new = f"{header.group(1)}{approved}{header.group(3)}[{', '.join(repr(r) for r in rejected)}]{header.group(5)}"
        source = source[:header.start()] + new + source[header.end():]
    return source


def describe(changes, catalog) -> str:
    """The current and proposed state of every affected accent (accents with no change are not listed; run with --status to see all twelve)."""
    if not changes:
        return 'No approvals, rejections or revocations: nothing would change; every second voice stays exactly as it is.'
    def state(accent, value):
        row = catalog.LISTENING_CANDIDATES[accent]
        names = {v: n for v, n, _ in row['second']}
        approved = f"{names[value['approved']]} ({value['approved']})" if value['approved'] else 'none (disabled)'
        return f"approved: {approved}; rejected: {[names[r] for r in value['rejected']] or 'none'}"
    rows = []
    for c in changes:
        label = NAMES[c['accent']]
        rows.append(f"{c['action'].upper():8} {label}\n    now:    {state(c['accent'], c['before'])}\n    after:  {state(c['accent'], c['after'])}")
    available = [NAMES[c['accent']] for c in changes if c['action'] == 'approve']
    tail = ('\nBecome available for same-accent dialogue (production flags are already on): ' + ', '.join(available)) if available else '\nNo accent becomes available for same-accent dialogue.'
    return '\n'.join(rows) + tail


def status(catalog) -> str:
    lines = []
    for accent, row in catalog.LISTENING_CANDIDATES.items():
        names = {v: n for v, n, _ in row['second']}
        lines.append(f"{NAMES[accent]:12} approved: {names[row['approved']] if row['approved'] else 'none (disabled)':40} rejected: {[names[r] for r in row['rejected']] or 'none'}")
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('decisions', nargs='?', help='file with the copied decisions ("-" for stdin)')
    parser.add_argument('--status', action='store_true', help='print the current state of all twelve accents and exit')
    parser.add_argument('--write', action='store_true', help='edit the catalog (default is a dry run)')
    parser.add_argument('--catalog', type=Path, default=CATALOG, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.status:
        print(status(load_catalog(args.catalog)))
        return 0
    if not args.decisions:
        parser.error('give a decisions file, or --status')
    text = sys.stdin.read() if args.decisions == '-' else Path(args.decisions).read_text()
    try:
        catalog = load_catalog(args.catalog)
        changes = plan(parse(text), catalog)
        updated = apply(args.catalog.read_text(), changes)
        if args.write and changes:
            args.catalog.write_text(updated)
            load_catalog(args.catalog).approved_second_voices()          # the edited catalog must still validate
    except ReviewError as error:
        print(f'Not applied: {error}')
        return 2
    print(describe(changes, catalog))
    print('\nWritten.' if args.write and changes else '\nDry run: nothing was changed.' if changes else '')
    if changes:
        print('Next: .venv/bin/pytest -q tests/test_listening_second_voices.py, open a PR, merge after CI. After deploy the approved accents appear for same-accent dialogue '
              '(AUDLI_LISTENING_FORMATS_ENABLED and the accent in AUDLI_LISTENING_FORMAT_ACCENTS are already set).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
