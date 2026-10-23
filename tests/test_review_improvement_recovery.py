"""Bounded recovery for language-coaching rejections (AUD-77): one automatic formatting repair, one learner retry,
prompt-version recovery, explicit error states and rejection-code monitoring. Mock providers only.

The bounds under test, per attempt and per exercise:
- at most ONE automatic repair, only for narrowly defined formatting failures, never for meaning-protective rejections;
- at most ONE learner-initiated retry of a rejected result per exercise per prompt version, counted against the rolling allowance;
- a prompt-version change never bypasses the allowance, cooldown or burst limits.
"""
import asyncio
import logging
import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text, update

from app.improvement import (FORMAT_FAILURES, LanguageCoaching, Note, PROMPT_VERSION, REPAIR_REMINDER, RETRY_CAP, rejection_layer, source_hash)
from app.storage import schema as s
from app.telemetry import aggregator
from test_auth import USER_A  # noqa: F401
from test_persistence import repository_factory  # noqa: F401
from test_review_improvement import (ANSWER, IMPROVED, T0, Clock, accounts_on, build, good, post, rows, seeded, tables)  # noqa: F401


def bad_format(text):
    """Edits with no notes: a formatting failure ('unexplained_changes'), not evidence about meaning."""
    return LanguageCoaching(improved_text=IMPROVED, notes=[], meaning_preserved=True, adds_information=False)


def flipped(text):
    """Reverses the meaning (adds a negation): a meaning-protective rejection."""
    return LanguageCoaching(improved_text="She didn't change the meeting to Monday because the team was sick",
        notes=[Note(kind='grammar', before='She change', after="She didn't change", why='x')], meaning_preserved=True, adds_information=False)


def retry(client, exercise_id, **body):
    return client.post(f'/api/exercises/{exercise_id}/review/improvement', json={'retry': True, **body})


def statuses(repo):
    return [(r['status'], r['rejection'], r['repaired'], r['prompt_version']) for r in rows(repo)]


# ---------------------------------------------------------------------------------------------- automatic repair
def test_only_the_three_formatting_failures_are_repairable():
    assert FORMAT_FAILURES == {'unexplained_changes', 'note_not_grounded', 'inconsistent_notes'}
    assert all(rejection_layer(code) == '2' for code in FORMAT_FAILURES)           # structure, never meaning evidence
    assert RETRY_CAP == 2 and PROMPT_VERSION == 'v3'


def test_a_formatting_failure_is_repaired_once_and_costs_no_extra_allowance(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=1)
    entry, ids = seeded(repo, exercise, count=2)
    before = tables(repo)
    provider.queue = [bad_format, good]
    state = post(client, ids[0]).json()['improvement']
    assert state['state'] == 'ready' and state['improved_text'] == IMPROVED
    assert provider.coached == [ANSWER, ANSWER] and provider.repairs == [None, REPAIR_REMINDER]
    assert len(provider.checked) == 1 and statuses(repo) == [('ready', None, True, PROMPT_VERSION)]
    assert post(client, ids[1]).status_code == 429          # the repair did not use a second allowance slot: one slot, one result
    assert REPAIR_REMINDER not in ANSWER and 'formatting' not in ' '.join(provider.coached)
    assert tables(repo) == before                            # the repair left transcript, evaluation, adaptation and evidence untouched


def test_a_failed_repair_is_never_repeated(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    provider.queue = [bad_format, bad_format, good]          # a third candidate exists but must never be requested
    state = post(client, ids[0]).json()['improvement']
    assert state['state'] == 'rejected' and state['reason'] == 'unexplained_changes' and state['retry_available'] is True
    assert provider.coached == [ANSWER, ANSWER] and provider.checked == [] and len(provider.queue) == 1
    assert statuses(repo) == [('rejected', 'unexplained_changes', True, PROMPT_VERSION)]


@pytest.mark.parametrize('make', [flipped, lambda t: LanguageCoaching(improved_text=IMPROVED, notes=[Note(kind='grammar', before='She change', after='She changed', why='x')],
                                                                         meaning_preserved=False, adds_information=False)])
def test_meaning_protective_rejections_are_never_retried_automatically(tmp_path, repository_factory, exercise, make):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    provider.queue = [make, good]
    state = post(client, ids[0]).json()['improvement']
    assert state['state'] == 'rejected' and rejection_layer(state['reason']) in ('1', '3')
    assert len(provider.coached) == 1 and provider.repairs == [None] and statuses(repo)[0][2] is False


def test_a_repaired_candidate_still_faces_the_independent_meaning_check(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    provider.queue = [bad_format, good]; provider.same = False
    state = post(client, ids[0]).json()['improvement']
    assert state['state'] == 'rejected' and state['reason'] == 'meaning_check_failed' and state['improved_text'] is None
    assert len(provider.coached) == 2 and len(provider.checked) == 1


def test_a_provider_error_during_repair_is_an_explicit_failure_that_costs_no_allowance(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=1)
    entry, ids = seeded(repo, exercise, count=2)
    provider.queue = [bad_format, ValueError('SECRET learner words')]
    response = post(client, ids[0])
    assert response.status_code == 503 and response.json()['improvement'] == {'code': 'improvement_failed'} and 'SECRET' not in response.text
    assert [r['status'] for r in rows(repo)] == ['failed']
    assert post(client, ids[1]).json()['improvement']['state'] == 'ready'            # the single slot is still free


# ---------------------------------------------------------------------------------------------- learner retry
def test_one_learner_retry_per_exercise_then_final(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    before = tables(repo)
    provider.coaching = flipped
    first = post(client, ids[0]).json()['improvement']
    assert first['state'] == 'rejected' and first['retry_available'] is True
    calls = len(provider.coached)
    assert post(client, ids[0]).json()['improvement'] == first and len(provider.coached) == calls      # re-reading is free
    second = retry(client, ids[0]).json()['improvement']                                                # the one retry
    assert second['state'] == 'rejected' and second['retry_available'] is False and len(provider.coached) == calls + 1
    final = retry(client, ids[0])                                                                       # no third attempt, no call
    assert final.status_code == 200 and final.json()['improvement']['state'] == 'rejected' and final.json()['improvement']['retry_available'] is False
    assert len(provider.coached) == calls + 1 and [r['status'] for r in rows(repo)] == ['rejected', 'rejected']
    report = client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement']
    assert report['state'] == 'rejected' and report['retry_available'] is False
    assert tables(repo) == before                                    # transcript, evaluation, adaptation and evidence untouched


def test_a_retry_can_recover_and_the_later_result_wins(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    before = tables(repo)
    provider.queue = [flipped]
    assert post(client, ids[0]).json()['improvement']['state'] == 'rejected'
    provider.coaching = good
    state = retry(client, ids[0]).json()['improvement']
    assert state['state'] == 'ready' and state['retry_available'] is False and state['improved_text'] == IMPROVED
    assert [r['status'] for r in rows(repo)] == ['rejected', 'ready']
    assert client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement']['state'] == 'ready'
    calls = len(provider.coached)
    assert retry(client, ids[0]).json()['improvement']['state'] == 'ready' and len(provider.coached) == calls   # a stored suggestion is never regenerated
    assert tables(repo) == before                            # a recovered retry also leaves the original answer and evaluation untouched


def test_a_retry_before_any_rejection_is_just_the_first_request(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    assert retry(client, ids[0]).json()['improvement']['state'] == 'ready' and len(provider.coached) == 1


def test_a_retry_counts_against_the_rolling_allowance_and_cannot_bypass_it(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=2)
    entry, ids = seeded(repo, exercise, count=2)
    provider.coaching = flipped
    assert post(client, ids[0]).json()['improvement']['state'] == 'rejected'     # 1 of 2
    provider.coaching = good
    assert post(client, ids[1]).json()['improvement']['state'] == 'ready'        # 2 of 2
    blocked = retry(client, ids[0])
    assert blocked.status_code == 429 and blocked.json()['improvement']['code'] == 'improvement_limit'
    assert [r['status'] for r in rows(repo)] == ['rejected', 'ready']             # no new row, no provider call
    app.state.review_clock.at = T0 + timedelta(hours=24, seconds=5)
    assert retry(client, ids[0]).json()['improvement']['state'] == 'ready'


def test_a_failed_retry_is_an_explicit_error_with_the_normal_cooldown(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    provider.queue = [flipped]
    assert post(client, ids[0]).json()['improvement']['state'] == 'rejected'
    provider.queue = [TimeoutError()]
    failed = retry(client, ids[0])
    assert failed.status_code == 503 and failed.json()['improvement']['code'] == 'improvement_failed'
    throttled = retry(client, ids[0])
    assert throttled.status_code == 429 and throttled.json()['improvement']['code'] == 'improvement_cooldown'
    app.state.review_clock.at += timedelta(seconds=31)
    assert retry(client, ids[0]).json()['improvement']['state'] == 'ready'
    assert [r['status'] for r in rows(repo)] == ['rejected', 'failed', 'ready']


def test_concurrent_retries_make_one_attempt(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    provider.queue = [flipped]
    assert post(client, ids[0]).json()['improvement']['state'] == 'rejected'
    provider.coaching = good; provider.delay = .1
    before = len(provider.coached)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*[http.post(f'/api/exercises/{ids[0]}/review/improvement', json={'retry': True}) for _ in range(6)])
    responses = asyncio.run(run())
    assert {r.status_code for r in responses} <= {200, 202} and len(provider.coached) == before + 1
    assert [r['status'] for r in rows(repo)] == ['rejected', 'ready']


# ---------------------------------------------------------------------------------------------- prompt-version recovery
def old_version_rejections(repo, exercise_id, count, version='v1', claimed_at=None):
    with repo.engine.connect() as db:
        attempt = db.execute(select(s.attempts.c.id).where(s.attempts.c.exercise_id == exercise_id)).scalar_one()
    with repo.transaction() as db:
        for index in range(count):
            db.execute(s.review_improvements.insert().values(id=str(uuid4()), user_id=repo.learner_id, exercise_id=exercise_id, attempt_id=attempt,
                status='rejected', rejection='negation_changed', source_hash=source_hash(ANSWER), prompt_version=version,
                claimed_at=(claimed_at or T0 - timedelta(days=3)) + timedelta(minutes=index), finished_at=T0 - timedelta(days=3)))


def test_older_prompt_version_rejections_become_retryable_without_bypassing_limits(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=1)
    entry, ids = seeded(repo, exercise, count=2)
    old_version_rejections(repo, ids[0], 2)                                         # two old-version rejections: terminal before the change
    passage = next(p for p in client.get(f'/api/review/lesson/{entry}').json()['passages'] if p['exercise_id'] == ids[0])   # passage order is not defined
    state = passage['improvement']
    assert state['state'] == 'rejected' and state['retry_available'] is True        # the cap counts only the current prompt version
    # Another learner-visible attempt this window uses the only allowance slot...
    assert post(client, ids[1]).json()['improvement']['state'] == 'ready'
    blocked = retry(client, ids[0])
    assert blocked.status_code == 429 and blocked.json()['improvement']['code'] == 'improvement_limit'   # ...so recovery cannot bypass it
    assert len(provider.coached) == 1
    app.state.review_clock.at = T0 + timedelta(hours=25)
    assert retry(client, ids[0]).json()['improvement']['state'] == 'ready'


def test_each_prompt_version_gets_its_own_two_attempts(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    old_version_rejections(repo, ids[0], 2)
    provider.coaching = flipped
    first = retry(client, ids[0]).json()['improvement']
    assert first['state'] == 'rejected' and first['retry_available'] is True      # 1 of 2 under the current version
    assert retry(client, ids[0]).json()['improvement']['retry_available'] is False   # 2 of 2
    assert [(r['status'], r['prompt_version']) for r in rows(repo)] == [('rejected', 'v1'), ('rejected', 'v1'), ('rejected', PROMPT_VERSION), ('rejected', PROMPT_VERSION)]


# ---------------------------------------------------------------------------------------------- request validation, isolation, independence
def test_request_body_is_strict(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    for body in ({'retry': 'yes'}, {'retry': 1}, {'retry': True, 'extra': 1}, {'learner_id': str(uuid4())}):
        assert client.post(f'/api/exercises/{ids[0]}/review/improvement', json=body).status_code == 422, body
    assert provider.coached == [] and rows(repo) == []
    assert client.post(f'/api/exercises/{ids[0]}/review/improvement', json={}).json()['improvement']['state'] == 'ready'


def test_retry_is_owner_scoped_and_independent_of_the_lesson_allowance(accounts_on, exercise):
    a, b, _ = accounts_on
    a.get('/api/profile'); b.get('/api/profile')
    a.app.state.review_clock = Clock()
    owner = a.app.state.repository.for_learner(USER_A)
    _, ids = seeded(owner, exercise)
    a.provider.coaching = flipped
    assert post(a, ids[0]).json()['improvement']['state'] == 'rejected'
    assert retry(b, ids[0]).status_code == 404                                      # not theirs: no claim, no call
    assert len(a.provider.coached) == 1 and [r['status'] for r in rows(owner)] == ['rejected']

    def forbidden(*args, **kwargs):
        raise AssertionError('recovery must not consult the lesson allowance')
    for name in ('admit', 'admit_exercise', 'admit_legacy', 'block_onboarding_restart'):
        setattr(a.app.state.limits, name, forbidden)
    a.provider.coaching = good
    assert retry(a, ids[0]).json()['improvement']['state'] == 'ready'


# ---------------------------------------------------------------------------------------------- explicit error states and monitoring
def test_busy_and_failure_responses_carry_stable_codes(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_max_concurrent=1)
    entry, ids = seeded(repo, exercise, count=2)
    provider.delay = .15

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*[http.post(f'/api/exercises/{i}/review/improvement') for i in ids])
    responses = asyncio.run(run())
    busy = [r for r in responses if r.status_code == 503]
    assert len(busy) == 1 and busy[0].json()['improvement'] == {'code': 'improvement_busy'}


def test_outcomes_are_logged_and_counted_by_rejection_code_without_any_text(tmp_path, repository_factory, exercise, caplog):
    app, client, provider, repo = build(tmp_path, repository_factory, latency_aggregates=True)
    entry, ids = seeded(repo, exercise)
    aggregator.reset()
    provider.queue = [bad_format, flipped]
    with caplog.at_level(logging.INFO):
        post(client, ids[0])
        provider.coaching = good
        retry(client, ids[0])
    logged = '\n'.join(record.getMessage() for record in caplog.records if 'review_improvement' in record.getMessage())
    assert 'outcome=rejected rejection=negation_changed layer=3 repaired=True retry=False' in logged
    assert 'outcome=ready rejection=None layer=None repaired=False retry=True' in logged
    assert ANSWER not in logged and IMPROVED not in logged and 'doctor' not in logged
    names = set(aggregator.snapshot()['stages'])
    assert {'review_improvement.rejected', 'review_improvement.rejected.layer3', 'review_improvement.repaired', 'review_improvement.ready'} <= names


def run_report(repo):
    """Execute scripts/review_improvement_report.sql statement by statement and return each result set."""
    sql = '\n'.join(line for line in Path('scripts/review_improvement_report.sql').read_text().splitlines() if not line.lstrip().startswith('--'))
    results = []
    with repo.engine.connect() as db:
        for statement in [part.strip() for part in sql.split(';') if part.strip()]:
            results.append([tuple(row) for row in db.execute(text(statement)).all()])
    return results


def test_the_monitoring_report_summarizes_outcomes_without_learner_data(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise, count=3)
    provider.queue = [flipped]
    post(client, ids[0])                                    # rejected
    provider.coaching = good
    retry(client, ids[0])                                   # recovered by a retry
    provider.queue = [bad_format, good]
    post(client, ids[1])                                    # repaired and ready
    provider.queue = [flipped, flipped]
    post(client, ids[2]); retry(client, ids[2])             # rejected twice: final
    outcomes, rates, retries = run_report(repo)
    assert sum(row[-1] for row in outcomes) == 5
    per_version = {row[0]: row for row in rates}
    version, attempts, ready, rejected, failed, repaired, pct = per_version[PROMPT_VERSION]
    assert (attempts, ready, rejected, failed, repaired) == (5, 2, 3, 0, 1) and float(pct) == pytest.approx(60.0, abs=.1)
    assert [(row[0], row[1], row[2]) for row in retries] == [(PROMPT_VERSION, 2, 1)]
    assert ANSWER not in repr((outcomes, rates, retries))
