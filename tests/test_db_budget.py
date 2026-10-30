"""AUD-84: database round-trip regression budgets.

A complete synthetic lesson is driven over the HTTP app with the production instrumentation switched on; each route's
worst-case statements, pool checkouts and transactions must stay within its ceiling. Ceilings are the measured values on
2026-10-24 (SQLite and real PostgreSQL 17). Counts are deterministic, so a failure means the code now does more database
work on that route: either undo it or lower/raise the ceiling in the same change, deliberately. Every statement and
transaction is a ~72 ms round trip in production (Singapore to Tokyo), plus one more for the ping and one for BEGIN/end.
"""
import pytest

from app.config import Settings
from app.main import create_app
from app.telemetry import aggregator
from conftest import ASGIClient, FakeProvider
from lesson_flow import run_lesson
from test_persistence import repository_factory  # noqa: F401  (fixture)

# route: (statements, checkouts, transactions) per call, worst case
BUDGETS = {
    'sqlite': {
        'GET /api/eligibility': (0, 0, 0), 'GET /api/exercises/{exercise_id}/audio': (2, 2, 2),
        'GET /api/exercises/{exercise_id}/conversation': (11, 11, 11), 'GET /api/lessons/current': (1, 1, 1),
        'GET /api/plan': (12, 4, 5), 'GET /api/profile': (1, 1, 1),
        'POST /api/attempts/{attempt_id}/assess': (67, 21, 21), 'POST /api/exercises/{exercise_id}/attempts': (25, 7, 7),
        'POST /api/exercises/{exercise_id}/coach-audio': (9, 5, 5), 'POST /api/exercises/{exercise_id}/conversation/listened': (12, 10, 10),
        'POST /api/exercises/{exercise_id}/conversation/ready': (32, 14, 14), 'POST /api/lessons/start': (17, 7, 8),
        'POST /api/lessons/{lesson_id}/answer': (4, 2, 2), 'POST /api/lessons/{lesson_id}/attempts': (4, 2, 2),
        'POST /api/lessons/{lesson_id}/exercise': (22, 12, 12), 'POST /api/lessons/{lesson_id}/heard': (6, 4, 4),
        'POST /api/recognition/acknowledgment-audio': (5, 3, 3), 'POST /api/telemetry/turn': (0, 0, 0),
    },
    'postgresql': {
        'GET /api/eligibility': (0, 0, 0), 'GET /api/exercises/{exercise_id}/audio': (2, 2, 2),
        'GET /api/exercises/{exercise_id}/conversation': (11, 11, 11), 'GET /api/lessons/current': (1, 1, 1),
        'GET /api/plan': (11, 4, 4), 'GET /api/profile': (1, 1, 1),
        'POST /api/attempts/{attempt_id}/assess': (65, 21, 21), 'POST /api/exercises/{exercise_id}/attempts': (23, 7, 7),
        'POST /api/exercises/{exercise_id}/coach-audio': (8, 5, 5), 'POST /api/exercises/{exercise_id}/conversation/listened': (11, 10, 10),
        'POST /api/exercises/{exercise_id}/conversation/ready': (31, 14, 14), 'POST /api/lessons/start': (15, 7, 7),
        'POST /api/lessons/{lesson_id}/answer': (3, 2, 2), 'POST /api/lessons/{lesson_id}/attempts': (3, 2, 2),
        'POST /api/lessons/{lesson_id}/exercise': (21, 12, 12), 'POST /api/lessons/{lesson_id}/heard': (5, 4, 4),
        'POST /api/recognition/acknowledgment-audio': (4, 3, 3), 'POST /api/telemetry/turn': (0, 0, 0),
    },
}


def drive_lesson(repository_factory, tmp_path, audio_bytes, **settings):
    aggregator.reset()
    repo = repository_factory()
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    app = create_app(Settings(data_dir=tmp_path, latency_aggregates=True, ack_lease_exempt=False, db_state_only_checkpoints=False, _env_file=None, **settings), provider, repo)
    client = ASGIClient(app, provider)
    ready = run_lesson(client, audio_bytes)
    return repo, client, ready, aggregator.snapshot()


def test_every_route_stays_within_its_database_budget(repository_factory, tmp_path, audio_bytes):
    repo, _, _, snapshot = drive_lesson(repository_factory, tmp_path, audio_bytes)
    budget = BUDGETS[repo.engine.dialect.name]
    routes = snapshot['routes']
    assert set(routes) == set(budget), f'new or missing routes: {sorted(set(routes) ^ set(budget))}; add a budget for new ones'
    over = {name: {'queries': (r['queries_max'], budget[name][0]), 'checkouts': (r['checkouts_max'], budget[name][1]),
                   'transactions': (r['transactions_max'], budget[name][2])}
            for name, r in routes.items()
            if (r['queries_max'], r['checkouts_max'], r['transactions_max']) > budget[name]
            and any(a > b for a, b in zip((r['queries_max'], r['checkouts_max'], r['transactions_max']), budget[name]))}
    assert not over, f'database work grew (actual, budget): {over}'


def test_budgets_are_tight(repository_factory, tmp_path, audio_bytes):
    """A budget more than a few statements above reality hides regressions: ratchet it down when the code gets leaner."""
    repo, _, _, snapshot = drive_lesson(repository_factory, tmp_path, audio_bytes)
    budget = BUDGETS[repo.engine.dialect.name]
    slack = {name: (r['queries_max'], budget[name][0]) for name, r in snapshot['routes'].items() if budget[name][0] - r['queries_max'] > 3}
    assert not slack, f'budget has slack, lower it (actual, budget): {slack}'
