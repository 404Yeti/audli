#!/usr/bin/env python3
"""Application smoke test against restored data. Read-only calls only. Run by 04-rehearse.sh --app-smoke on the rehearsal copy.

For EVERY learner it checks that the repository's eligibility (observe mode) agrees with an independent reading of that
learner's latest lesson start; for a sample of learners it boots the real application in off and observe mode and calls the
read endpoints. Output: PASS|name|detail or FAIL|name|detail; exit status 1 on any FAIL.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url-var', required=True)
    parser.add_argument('--db-name', required=True)
    args = parser.parse_args()
    url = make_url(os.environ[args.url_var].replace('postgresql+psycopg://', 'postgresql://', 1)).set(database=args.db_name)
    url_text = url.render_as_string(hide_password=False)
    from app.config import Settings
    from app.main import create_app
    from app.repository import PostgresRepository
    results = []
    check = lambda ok, name, detail='': results.append(('PASS' if ok else 'FAIL', name, detail))

    repo = PostgresRepository(url_text)
    with repo.engine.connect() as db:
        learners = [str(row[0]) for row in db.execute(text('SELECT id FROM users ORDER BY id'))]
        latest = {str(r[0]): (r[1], r[2]) for r in db.execute(text(
            "SELECT DISTINCT ON (user_id) user_id, started_at, status FROM lesson_lifecycles ORDER BY user_id, started_at DESC, id DESC"))}
    at = datetime.now(timezone.utc)
    wrong = []
    kinds = {'eligible': [], 'cooldown': [], 'lesson_in_progress': []}
    for uid in learners:
        got = repo.for_learner(uid).eligibility('observe', at)
        started, status = latest.get(uid, (None, None))
        if started is None:
            expected = 'eligible'
        elif status in ('active', 'paused'):
            expected = 'lesson_in_progress'
        else:
            expected = 'cooldown' if at < started + timedelta(hours=24) else 'eligible'
        if got.reason != expected or (expected == 'cooldown' and abs((got.next_available_at - (started + timedelta(hours=24))).total_seconds()) > 1):
            wrong.append(f'{uid}: {got.reason} vs {expected}')
        kinds[expected].append(uid)
    repo.close()
    check(not wrong, 'eligibility_matches_latest_lesson_for_every_learner', f'{len(learners)} learners; ' + (f'{len(wrong)} differ, e.g. {wrong[0]}' if wrong else
          f"{len(kinds['eligible'])} eligible, {len(kinds['cooldown'])} in cooldown, {len(kinds['lesson_in_progress'])} with an open lesson"))

    async def call(mode, uid):
        settings = Settings(_env_file=None, persistence='postgres', database_url=url_text, auth_mode='local', learner_id=uid,
                            provider='demo', lesson_limit_mode=mode, data_dir=Path('/tmp/audli-smoke-data'))
        app = create_app(settings)
        statuses = {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://smoke') as client:
            for path in ('/api/eligibility', '/api/lessons/current', '/api/history', '/api/review', '/api/profile', '/api/onboarding/reset-controls'):
                statuses[path] = (await client.get(path)).status_code
            body = (await client.get('/api/eligibility')).json()
        app.state.repository.close()
        return statuses, body

    sample = [uids[0] for uids in kinds.values() if uids]
    for mode in ('off', 'observe'):
        for uid in sample:
            statuses, body = asyncio.run(call(mode, uid))
            bad = {p: s for p, s in statuses.items() if s != 200}
            ok = not bad and body['mode'] == mode and body['enforced'] is False and (mode != 'off' or body['eligible'] is True)
            check(ok, f'app_{mode}_mode_read_endpoints', f'learner {uid[-4:]}: {"all 200" if not bad else bad}; eligibility {body["reason"]}')
    for status, name, detail in results:
        print(f'{status}|{name}|{detail}')
    sys.exit(1 if any(r[0] == 'FAIL' for r in results) else 0)


main()
