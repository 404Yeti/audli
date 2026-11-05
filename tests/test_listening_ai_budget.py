"""The global AI spending ceiling and kill switch: fail-closed reservations, concurrency safety, halting, settlement, and that normal lessons
(no AI configured, or an ample budget) behave exactly as before. No network and no paid call: the OpenAI SDK talks to httpx.MockTransport."""
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.listening import schema as l
from app.listening.ai_budget import (MAX_CALL_MICRO, AiGuard, BudgetRefused, GuardedAssessor, GuardedTranscriber, text_micro, transcription_micro, usd_to_micro)
from app.listening.providers import FakeAssessor, FakeTranscriber, ProviderPermanent, build_providers
from app.listening.worker import run_once
from app.main import create_app
from app.storage.repository import utcnow
from conftest import ASGIClient, FakeProvider
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import PEOPLE_WORDS, begin, by_kind, journey, refresh, spoken_action, start_pending, stored, tone, upload  # noqa: F401  (fixtures)
from test_listening_providers import POINTS, error_body, responses_body

OPENAI = dict(listening_provider='openai', OPENAI_API_KEY='sk-test-not-real', _env_file=None)


def guard_for(j, usd=1.0, clock=None, **settings):
    return AiGuard(j.engine, Settings(listening_ai_daily_budget_usd=usd, **{**OPENAI, **settings}), **({'clock': clock} if clock else {}))


def reserved(j, scope=None):
    with j.engine.connect() as db:
        row = db.execute(select(l.ai_limits).where(l.ai_limits.c.scope == (scope or datetime.now(timezone.utc).strftime('%Y-%m-%d')))).mappings().first()
    return (row['reserved_micro'], row['calls']) if row else (0, 0)


# ---- reservations ----------------------------------------------------------------------------------------------------------------

def test_a_request_that_does_not_fit_is_refused_and_changes_nothing(journey):
    guard = guard_for(journey, usd=0.01)                                       # 10,000 micro-dollars
    guard.reserve('assessment', 4000), guard.reserve('assessment', 4000)
    with pytest.raises(BudgetRefused) as refused:
        guard.reserve('assessment', 4000)
    assert refused.value.code == 'ai_budget' and reserved(journey) == (8000, 2)           # the refusal left no trace
    guard.reserve('assessment', 2000)                                                      # exactly the remainder still fits
    assert reserved(journey) == (10000, 3)
    with pytest.raises(BudgetRefused):
        guard.reserve('assessment', 1)


def test_the_guard_fails_closed_on_anything_it_cannot_price_or_check(journey):
    guard = guard_for(journey)
    for model in ('gpt-unknown', 'gpt-9-future'):
        with pytest.raises(BudgetRefused) as unpriced:
            text_micro(model, 1000, 700)
        assert unpriced.value.code == 'ai_budget'
        with pytest.raises(BudgetRefused):
            transcription_micro(model)
    for micro in (0, -5, MAX_CALL_MICRO + 1):
        with pytest.raises(BudgetRefused):
            guard.reserve('assessment', micro)                                             # an implausible estimate is a bug, not a request
    broken = AiGuard(SimpleNamespace(begin=lambda: (_ for _ in ()).throw(RuntimeError('database down'))), Settings(**OPENAI))
    with pytest.raises(BudgetRefused) as down:
        broken.reserve('assessment', 100)
    assert down.value.code == 'ai_budget' and broken.halted() is True and broken.accepting() == (False, 'paused')
    assert reserved(journey) == (0, 0)


def test_worst_case_estimates_come_from_the_price_table():
    assert transcription_micro('gpt-transcribe') == 4500                                   # 60 s at US$0.0045 per minute
    assert transcription_micro('gpt-transcribe', 30) == 2250
    assert usd_to_micro(2.0) == 2_000_000
    assert 300 < text_micro('gpt-6-luna', 2000, 700) < 1000                                # well under a cent
    assert text_micro('gpt-6.1-terra', 2000, 700) > text_micro('gpt-6-luna', 2000, 700)


def test_settlement_replaces_the_reservation_with_the_measured_cost(journey):
    guard = guard_for(journey)
    cheaper = guard.reserve('assessment', 1000)
    guard.settle(cheaper, 300)
    assert reserved(journey)[0] == 300
    refused = guard.reserve('assessment', 1000)
    guard.settle(refused, release=True)                                                   # the provider refused the request: nothing was spent
    assert reserved(journey)[0] == 300
    kept = guard.reserve('assessment', 1000)
    guard.settle(kept, None)                                                              # unknown cost (e.g. a timeout) stays reserved
    assert reserved(journey)[0] == 1300
    guard.settle(kept, 5_000_000_000 * -1)                                                # never below zero, never raises
    assert reserved(journey)[0] == 0


def test_each_utc_day_has_its_own_ceiling(journey):
    day_one = datetime(2026, 11, 4, 23, 59, tzinfo=timezone.utc)
    clock = {'now': day_one}
    guard = guard_for(journey, usd=0.005, clock=lambda: clock['now'])
    guard.reserve('assessment', 5000)
    with pytest.raises(BudgetRefused):
        guard.reserve('assessment', 1)
    clock['now'] = day_one + timedelta(minutes=2)
    guard.reserve('assessment', 5000)                                                      # a new UTC day, a fresh ceiling
    assert reserved(journey, '2026-11-04') == (5000, 1) and reserved(journey, '2026-11-05') == (5000, 1)


def test_concurrent_requests_can_never_reserve_past_the_ceiling(journey):
    guard = guard_for(journey, usd=0.01)                                                   # room for exactly 3 of 3,000
    outcomes, lock = [], threading.Lock()

    def attempt():
        try:
            guard.reserve('assessment', 3000)
            result = 'ok'
        except BudgetRefused:
            result = 'refused'
        with lock:
            outcomes.append(result)
    threads = [threading.Thread(target=attempt) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count('ok') == 3 and outcomes.count('refused') == 13
    assert reserved(journey) == (9000, 3)


# ---- kill switch -----------------------------------------------------------------------------------------------------------------

def test_the_environment_switch_stops_everything(journey):
    guard = guard_for(journey, listening_ai_enabled=False)
    assert guard.halted() is True and guard.accepting() == (False, 'paused')
    with pytest.raises(BudgetRefused) as off:
        guard.reserve('assessment', 100)
    assert off.value.code == 'ai_halted' and reserved(journey) == (0, 0)


def test_the_database_flag_halts_new_requests_without_a_deploy_and_resumes(journey):
    operator, worker = guard_for(journey), guard_for(journey)                               # two separate processes in real life
    reservation = worker.reserve('assessment', 1000)                                         # in flight before the halt
    operator.set_halted(True)
    assert worker.halted() is True and worker.accepting() == (False, 'paused')
    with pytest.raises(BudgetRefused) as halted:
        worker.reserve('assessment', 1000)
    assert halted.value.code == 'ai_halted'
    worker.settle(reservation, 400)                                                          # a request already in flight still settles
    assert reserved(journey)[0] == 400
    operator.set_halted(False)
    assert worker.accepting() == (True, '') and worker.reserve('assessment', 1000)
    status = operator.status()
    assert status['halted'] is False and status['calls'] == 2 and 'not billing' in status['note'] and status['daily_ceiling_usd'] == 1.0


def test_status_and_operator_commands_print_no_secrets(journey, capsys, monkeypatch):
    import app.config as config
    import app.repository as repository
    from app.listening import ai_budget
    monkeypatch.setattr(config, 'Settings', lambda: Settings(listening_ai_daily_budget_usd=1.0, **OPENAI))
    monkeypatch.setattr(repository, 'create_repository', lambda settings: SimpleNamespace(engine=journey.engine))
    assert ai_budget.main(['halt']) == 0 and json.loads(capsys.readouterr().out)['halted'] is True
    assert ai_budget.main(['resume']) == 0 and json.loads(capsys.readouterr().out)['halted'] is False
    assert ai_budget.main(['status']) == 0 and 'sk-test' not in capsys.readouterr().out
    assert ai_budget.main(['bogus']) == 2


# ---- through the worker and the upload API -------------------------------------------------------------------------------------

def mock_openai(usage=True, hits=None):
    def handler(request):
        if hits is not None:
            hits.append(request.url.path)
        if request.url.path.endswith('/audio/transcriptions'):
            body = {'text': PEOPLE_WORDS, 'languages': [{'code': 'en'}]}
            return httpx.Response(200, json={**body, **({'usage': {'type': 'duration', 'seconds': 3}} if usage else {})})
        body = responses_body(json.dumps({'points': [{'id': p['id'], 'state': 'missing', 'quote': None} for p in POINTS]}))
        return httpx.Response(200, json={**body, **({'usage': {'input_tokens': 1000, 'output_tokens': 200}} if usage else {})})
    return handler


def guarded(j, guard, handler):
    return build_providers(guard.settings, guard, httpx.Client(transport=httpx.MockTransport(handler)))


def drive(j, heard, judge, steps=1):
    start = utcnow()
    return [run_once(j.engine, j.recordings, heard, judge, 'w', now=start + timedelta(minutes=30 * n)) for n in range(steps)]


def test_normal_lessons_are_unchanged_and_spend_is_the_measured_cost(journey):
    j = journey
    view = start_pending(j)
    guard = guard_for(j, usd=2.0)
    heard, judge = guarded(j, guard, mock_openai())
    assert isinstance(heard, GuardedTranscriber) and isinstance(judge, GuardedAssessor)
    assert drive(j, heard, judge) == ['done']
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'assessed'
    assert reserved(j) == (225 + 200, 2)                      # 3 s of audio at US$0.0045/min plus 1,000/200 tokens on gpt-6-luna, not the worst-case reservations


def test_an_exhausted_budget_sends_no_request_and_the_answer_fails_visibly_and_cheaply(journey):
    j = journey
    view = start_pending(j)
    hits = []
    guard = guard_for(j, usd=0.001)                              # 1,000 micro-dollars: less than the 4,500 a worst-case transcription reserves
    heard, judge = guarded(j, guard, mock_openai(hits=hits))
    outcomes = [o for o in drive(j, heard, judge, steps=6) if o]
    assert outcomes == ['retry', 'retry', 'retry', 'failed'] and hits == []                  # not one request left the building
    row, job = stored(j)
    assert row['failure_code'] == 'ai_budget' and row['recording_key'] is None and reserved(j) == (0, 0)
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['failure_code'] == 'provider_unavailable'   # coarse for the learner


def test_halting_in_flight_work_retries_and_resuming_completes_it(journey):
    j = journey
    start_pending(j)
    hits = []
    guard = guard_for(j)
    heard, judge = guarded(j, guard, mock_openai(hits=hits))
    guard.set_halted(True)
    assert drive(j, heard, judge) == ['retry'] and hits == []
    guard.set_halted(False)
    assert drive(j, heard, judge, steps=2)[-1] == 'done' and len(hits) == 2


def test_a_provider_refusal_is_released_but_a_timeout_keeps_its_reservation(journey):
    j = journey
    guard = guard_for(j)

    def refuse(request):
        return httpx.Response(401, json=error_body(401, 'invalid_api_key'))
    heard, _ = guarded(j, guard, refuse)
    with pytest.raises(ProviderPermanent):
        heard.transcribe(b'x' * 300, 'audio/webm')
    assert reserved(j)[0] == 0

    def slow(request):
        raise httpx.ReadTimeout('slow')
    heard, _ = guarded(j, guard, slow)
    with pytest.raises(Exception):
        heard.transcribe(b'x' * 300, 'audio/webm')
    assert reserved(j)[0] == 4500                                # a timed-out request may still be billed: stay conservative


def test_real_providers_cannot_be_built_without_the_guard(journey):
    with pytest.raises(RuntimeError, match='spending guard'):
        build_providers(Settings(**OPENAI))
    heard, judge = build_providers(Settings(**OPENAI), guard_for(journey))
    assert isinstance(heard, GuardedTranscriber) and isinstance(judge, GuardedAssessor)
    fake_heard, fake_judge = build_providers(Settings(_env_file=None))
    assert isinstance(fake_heard, FakeTranscriber) and isinstance(fake_judge, FakeAssessor)        # nothing to guard: no money moves


def api(j, usd, **settings):
    app = create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, listening_ai_daily_budget_usd=usd, **{**OPENAI, **settings}),
                     FakeProvider(), repository=j.repo)
    return ASGIClient(app, None)


def test_uploads_are_refused_politely_while_halted_or_out_of_budget_and_accepted_otherwise(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    paused = 'Spoken answers are paused'
    halted = api(j, 2.0)
    guard_for(j).set_halted(True)
    refused = upload(halted, view)
    assert refused.status_code == 503 and paused in refused.json()['detail'] and 'budget' not in refused.text and 'sk-' not in refused.text
    assert recordings_none(j) and stored_rows(j) == 0
    guard_for(j).set_halted(False)
    broke = api(j, 0.005)                                         # 5,000 micro-dollars: below the US$0.01 floor for one more answer
    assert upload(broke, view).status_code == 503
    assert stored_rows(j) == 0
    ample = api(j, 2.0)
    assert upload(ample, view).status_code == 200 and stored_rows(j) == 1
    spoken_action(ample, view, 'skip')                           # other actions never depend on the AI budget


def recordings_none(j):
    folder = j.tmp / 'listening_recordings' / 'listening-tmp'
    return not folder.exists() or not any(p.is_file() for p in folder.rglob('*'))


def stored_rows(j):
    with j.engine.connect() as db:
        return len(db.execute(select(l.spoken.c.attempt_id)).all())


def test_the_fake_development_provider_ignores_the_budget_but_honours_the_kill_switch(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    off = ASGIClient(create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, listening_ai_enabled=False, _env_file=None),
                                FakeProvider(), repository=j.repo), None)
    switched_off = upload(off, view)
    assert switched_off.status_code == 503 and 'paused' in switched_off.json()['detail'] and stored_rows(j) == 0
    dev = ASGIClient(create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, listening_ai_daily_budget_usd=0, _env_file=None),
                                FakeProvider(), repository=j.repo), None)
    assert upload(dev, view).status_code == 200 and stored_rows(j) == 1       # budget 0, but nothing is spent on the fake provider


def test_check_again_is_also_gated(journey):
    j = journey
    view = start_pending(j)
    guard = guard_for(j)
    heard, judge = guarded(j, guard, lambda r: httpx.Response(400, json=error_body(400)))
    start = utcnow()
    run_once(j.engine, j.recordings, heard, judge, 'w', now=start)
    guard_for(j).set_halted(True)
    client = api(j, 2.0)
    again = spoken_action(client, view, 'retry')
    assert again.status_code == 503 and 'paused' in again.json()['detail']
