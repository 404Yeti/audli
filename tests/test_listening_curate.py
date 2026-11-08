"""Curator batch workflow (app/listening/curate.py) and the curator generation allowance (Ledger.cap_for). Fake providers throughout: no paid call.
Runs on SQLite always and on real PostgreSQL when AUDLI_TEST_POSTGRES_URL is set."""
import io
import threading
from contextlib import redirect_stdout
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text, update

from app.config import Settings
from app.listening import curate as c, generation as g, generation_jobs as gj, schema as t
from app.listening.generation import GenerationRefused, Ledger, Spec
from app.listening.generator import run_once
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation import lab, settings as gen_settings  # noqa: F401  (fixture)

PILOT_ACCENTS = 'canadian,japanese,scottish,indian'


def entry(key, stage='pilot', accent='american', **over):
    spec = {'topic': 'ordering a drink at a cafe', 'cefr': 'A1', 'accent': accent, 'minutes': 1, 'format': 'dialogue', 'scenario': 'a customer and a barista', **over}
    return {'slug': key, 'stage': stage, 'title': 'Ordering a drink', 'category': 'everyday', 'spec': spec}


def manifest(budget=1.0, keys=('k1', 'k2', 'k3'), locked_budget=0):
    return c.Manifest.model_validate({'manifest_id': 'test-1', 'budgets_usd': {'pilot': budget, 'later': locked_budget},
                                      'lessons': [entry(k) for k in keys] + [entry('z1', stage='later')]})


def curator_settings(user, **over):
    return gen_settings(listening_curator_ids=user, **{'listening_gen_curator_daily': 5, **over})


def run(fn, *args, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(*args, out=lambda line: buf.write(line + '\n'), **kw) if 'out' in fn.__code__.co_varnames else fn(*args, **kw)
    return code, buf.getvalue()


def work(lab, settings):
    deps = lab.deps(settings=settings)
    done = []
    while (outcome := run_once(deps, 'w')) is not None:
        done.append(outcome)
    return done


MANIFEST_FOR_ROWS = manifest()


def count_requests(lab, key):
    with lab.engine.connect() as db:
        return len([r for r in c.stage_rows(db, MANIFEST_FOR_ROWS, 'pilot') if r.client_request_id.startswith(f'curate:test-1:pilot:{key}:r')])


# ---- the manifest ---------------------------------------------------------------------------------------------------------------------

def test_shipped_manifest_has_twenty_lessons_and_exactly_the_five_pilot_ones_unlocked():
    m = c.load_manifest(c.DEFAULT_MANIFEST)
    assert len(m.lessons) == 20 and len({e.key for e in m.lessons}) == 20
    assert [e.key for e in m.entries('pilot')] == ['a1-cafe-drink', 'a2-office-coffee', 'a2-surprise-birthday', 'b1-weekend-trip', 'b1-moving-a-meeting']
    assert m.budgets_usd['pilot'] == 1.0 and all(v == 0 for k, v in m.budgets_usd.items() if k != 'pilot')
    levels = [e.spec.cefr for e in m.lessons]
    assert (levels.count('A1'), levels.count('A2'), levels.count('B1'), levels.count('B2')) == (5, 6, 6, 3)
    assert max(len(c.request_key(m, e, 99)) for e in m.lessons) <= 64


def test_manifest_rejects_unknown_fields_missing_budgets_duplicates_and_bad_keys():
    base = {'manifest_id': 'm', 'budgets_usd': {'pilot': 1}, 'lessons': [entry('a-1')]}
    c.Manifest.model_validate(base)
    for bad in ({**base, 'extra': 1}, {**base, 'budgets_usd': {}}, {**base, 'lessons': [entry('a-1'), entry('a-1')]}, {**base, 'lessons': [entry('Bad Key')]},
                {**base, 'budgets_usd': {'pilot': 99}}, {**base, 'lessons': [{**entry('a-1'), 'category': 'sports'}]}):
        with pytest.raises(ValidationError):
            c.Manifest.model_validate(bad)


def test_plan_reports_blocked_accents_and_formats_until_they_are_opened():
    m = c.load_manifest(c.DEFAULT_MANIFEST)
    closed = gen_settings()
    assert [bool(c.entry_blockers(e, closed)) for e in m.entries('pilot')] == [False, True, True, True, True]    # only the American café lesson exists today
    opened = gen_settings(listening_formats_enabled=True, listening_format_accents=PILOT_ACCENTS)
    assert [c.entry_blockers(e, opened) for e in m.entries('pilot')] == [[]] * 5


# ---- the curator allowance ----------------------------------------------------------------------------------------------------------

def test_cap_for_defaults_to_the_ordinary_cap_and_never_exceeds_five():
    user = str(uuid4())
    assert Ledger(None, gen_settings()).cap_for(user) == 2
    assert Ledger(None, gen_settings(listening_curator_ids=user)).cap_for(user) == 2                      # on the list but no allowance configured
    assert Ledger(None, curator_settings(user)).cap_for(user) == 5
    assert Ledger(None, curator_settings(user)).cap_for(user.upper()) == 5
    assert Ledger(None, curator_settings(str(uuid4()))).cap_for(user) == 2                                  # not on the list: ordinary
    assert Ledger(None, gen_settings(listening_curator_ids=user, listening_gen_curator_daily=1)).cap_for(user) == 2     # never below the ordinary cap
    with pytest.raises(ValidationError):
        gen_settings(listening_gen_curator_daily=6)


def test_availability_and_reservation_agree_and_enforce_the_curator_cap(lab):
    s = curator_settings(lab.owner)
    ledger = Ledger(lab.engine, s)
    for i in range(5):
        assert ledger.availability(lab.owner, 1, 1) == (True, '')
        request_id = new_request(lab, lab.owner, f'cap-{i}')
        ledger.reserve(lab.owner, request_id, 100, 1000)
    assert ledger.availability(lab.owner, 1, 1) == (False, 'daily_limit')
    with pytest.raises(GenerationRefused) as refused:
        ledger.reserve(lab.owner, new_request(lab, lab.owner, 'cap-6'), 100, 1000)
    assert refused.value.reason == 'daily_limit'
    ordinary = Ledger(lab.engine, s)
    for i in range(2):
        ordinary.reserve(lab.other, new_request(lab, lab.other, f'o-{i}'), 100, 1000)
    with pytest.raises(GenerationRefused):                                                                  # the ordinary learner stays at 2
        ordinary.reserve(lab.other, new_request(lab, lab.other, 'o-3'), 100, 1000)


def test_the_global_budget_beats_the_curator_cap(lab):
    s = curator_settings(lab.owner, listening_gen_daily_budget_usd=0.05)
    with pytest.raises(GenerationRefused) as refused:
        Ledger(lab.engine, s).reserve(lab.owner, new_request(lab, lab.owner, 'g-1'), 100, 60_000)
    assert refused.value.reason == 'budget'


def test_simultaneous_curator_reservations_cannot_pass_the_cap(lab):
    s = curator_settings(lab.owner)
    ids = [new_request(lab, lab.owner, f'race-{i}') for i in range(9)]
    results, gate = [], threading.Barrier(9)

    def attempt(request_id):
        gate.wait()
        try:
            Ledger(lab.engine, s).reserve(lab.owner, request_id, 100, 1000)
            results.append('ok')
        except GenerationRefused as refused:
            results.append(refused.reason)
    threads = [threading.Thread(target=attempt, args=(r,)) for r in ids]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert results.count('ok') <= 5
    if lab.engine.dialect.name == 'postgresql':
        assert results.count('ok') == 5 and results.count('daily_limit') == 4


def new_request(lab, user, name):
    with lab.engine.begin() as db:
        request_id, _ = gj.create_generation(db, user, name, Spec(**entry('x')['spec']), [], None)
    return request_id


# ---- plan is read-only ------------------------------------------------------------------------------------------------------------------

def snapshot(lab):
    with lab.engine.connect() as db:
        return {n: db.execute(text(f'SELECT count(*) FROM {n}')).scalar_one() for n in
                ('listening_generation_requests', 'listening_generation_limits', 'listening_jobs', 'listening_ai_limits', 'listening_lessons')}


def test_plan_writes_nothing_and_reports_the_state(lab):
    s = curator_settings(lab.owner)
    before = snapshot(lab)
    plan = c.build_plan(lab.engine, s, manifest(), 'pilot', lab.owner)
    assert snapshot(lab) == before
    assert plan['blockers'] == [] and len(plan['items']) == 3 and plan['budget_micro'] == 1_000_000
    assert any('curator allowance today: 0/5 used' in w for w in plan['warnings'])
    locked = c.build_plan(lab.engine, s, manifest(), 'later', lab.owner)
    assert any('locked' in b for b in locked['blockers'])
    outsider = c.build_plan(lab.engine, s, manifest(), 'pilot', lab.other)
    assert any('not on AUDLI_LISTENING_CURATOR_IDS' in b for b in outsider['blockers'])
    off = c.build_plan(lab.engine, Settings(_env_file=None, listening_enabled=True, listening_generation_enabled=False, listening_curator_ids=lab.owner), manifest(), 'pilot', lab.owner)
    assert any('not enabled' in b for b in off['blockers'])
    assert snapshot(lab) == before


# ---- submit: authorization, incremental, idempotent, resumable --------------------------------------------------------------------------

def test_submit_is_refused_without_authorization_and_spends_nothing(lab):
    s, before = curator_settings(lab.owner), snapshot(lab)
    for kwargs in ({'curator': lab.other, 'approve': '1.0'}, {'curator': lab.owner, 'approve': '0.5'}, {'curator': lab.owner, 'approve': 'lots'}):
        code, _ = run(c.submit, lab.engine, s, manifest(), 'pilot', **kwargs)
        assert code == 2
    code, _ = run(c.submit, lab.engine, s, manifest(), 'later', lab.owner, '0')                           # a locked stage can never be approved
    assert code == 2
    off = Settings(_env_file=None, listening_enabled=True, listening_generation_enabled=False, listening_curator_ids=lab.owner, listening_gen_curator_daily=5)
    assert run(c.submit, lab.engine, off, manifest(), 'pilot', lab.owner, '1.0')[0] == 2                  # generation not enabled in this environment
    assert snapshot(lab) == before


def test_submit_goes_one_lesson_at_a_time_and_a_rerun_never_duplicates(lab):
    s = curator_settings(lab.owner)
    m = manifest()
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0')
    assert code == 0 and 'submitted k1' in out and 'k2' not in out
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0')                                 # k1 still generating: wait, do not start k2
    assert code == 0 and 'waiting' in out and count_requests(lab, 'k2') == 0
    assert count_requests(lab, 'k1') == 1
    assert work(lab, s)
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0')
    assert 'skip k1: already ready' in out and 'submitted k2' in out
    assert count_requests(lab, 'k1') == 1 and count_requests(lab, 'k2') == 1
    with lab.engine.connect() as db:
        owners = db.execute(select(t.generation_requests.c.lesson_id).where(t.generation_requests.c.client_request_id.like('curate:test-1:pilot:k1:r%'))).scalar_one()
        assert db.execute(select(func.count()).select_from(t.lesson_owners).where((t.lesson_owners.c.lesson_id == owners) & (t.lesson_owners.c.user_id == lab.owner))).scalar_one() == 1


def test_a_refused_request_is_resumed_in_place_not_duplicated(lab):
    tight = curator_settings(lab.owner, listening_gen_per_learner_daily=1, listening_gen_curator_daily=None)
    m = manifest()
    assert run(c.submit, lab.engine, tight, m, 'pilot', lab.owner, '1.0')[0] == 0
    work(lab, tight)
    code, out = run(c.submit, lab.engine, tight, m, 'pilot', lab.owner, '1.0')
    assert code == 3 and 'refused (daily_limit)' in out and count_requests(lab, 'k2') == 1
    with lab.engine.connect() as db:
        first = db.execute(select(t.generation_requests.c.id, t.generation_requests.c.phase).where(t.generation_requests.c.client_request_id == 'curate:test-1:pilot:k2:r1')).one()
    assert first.phase == 'unavailable'
    roomy = curator_settings(lab.owner)
    code, out = run(c.submit, lab.engine, roomy, m, 'pilot', lab.owner, '1.0')
    assert code == 0 and f'request {first.id} queued' in out and count_requests(lab, 'k2') == 1


def fail_request(lab, key, attempt=1, credits=0):
    with lab.engine.begin() as db:
        db.execute(update(t.generation_requests).where(t.generation_requests.c.client_request_id == f'curate:test-1:pilot:{key}:r{attempt}')
                   .values(phase='failed', failure_code='provider_unavailable', reserved_micro=0, reserved_credits=0, actual_credits=credits))


def test_a_failed_lesson_is_never_retried_automatically_and_still_counts_against_the_budget(lab):
    s = curator_settings(lab.owner)
    m = manifest()
    assert run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0')[0] == 0
    fail_request(lab, 'k1', credits=400)
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0')
    assert code == 3 and 'Nothing is retried automatically' in out and count_requests(lab, 'k1') == 1 and count_requests(lab, 'k2') == 0
    assert run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0', retry='k2')[0] == 2                     # only a failed lesson can be retried
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0', retry='k1')
    assert code == 0 and 'submitted k1' in out and count_requests(lab, 'k1') == 2
    with lab.engine.connect() as db:
        spent = c.spent_micro(db, m, 'pilot', s)
    assert spent >= c.LLM_MICRO_PER_CALL + g.usd_micro_for_credits(400, s.listening_gen_usd_per_1k_credits) + gj.reservation_for(s, Spec(**entry('x')['spec']))[1]


def test_the_stage_budget_is_cumulative_across_attempts_and_restarts(lab):
    s = curator_settings(lab.owner)
    estimate = gj.reservation_for(s, Spec(**entry('x')['spec']))[1]
    failed_cost = c.LLM_MICRO_PER_CALL + g.usd_micro_for_credits(400, s.listening_gen_usd_per_1k_credits)
    tight = round((estimate + failed_cost - 1) / 1_000_000, 6)                       # one micro-dollar too little for failure + a new attempt
    m = manifest(budget=tight)
    assert run(c.submit, lab.engine, s, m, 'pilot', lab.owner, str(tight), only='k1')[0] == 0
    fail_request(lab, 'k1', credits=400)
    before = snapshot(lab)
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, str(tight), retry='k1')
    assert code == 3 and 'stage budget' in out and count_requests(lab, 'k1') == 1 and snapshot(lab) == before
    code, out = run(c.submit, lab.engine, s, m, 'pilot', lab.owner, str(tight), only='k2')                    # a different lesson is blocked by the same ledger
    assert code == 3 and 'stage budget' in out and count_requests(lab, 'k2') == 0


def test_only_one_submit_runs_per_manifest_on_postgresql(lab):
    if lab.engine.dialect.name != 'postgresql':
        pytest.skip('advisory locks are PostgreSQL-only')
    s = curator_settings(lab.owner)
    with lab.engine.connect() as holder:
        assert holder.execute(text("SELECT pg_try_advisory_lock(hashtext('audli-curate:test-1'))")).scalar()
        code, out = run(c.submit, lab.engine, s, manifest(), 'pilot', lab.owner, '1.0')
    assert code == 2 and 'another submit' in out


def test_status_names_the_next_operator_command_and_prints_no_secrets(lab):
    s = curator_settings(lab.owner)
    m = manifest()
    run(c.submit, lab.engine, s, m, 'pilot', lab.owner, '1.0')
    work(lab, s)
    code, out = run(c.status, lab.engine, s, m, 'pilot')
    assert code == 0 and 'k1' in out and 'ready' in out and 'python -m app.listening.library register --category everyday' in out
    source = open(c.__file__).read()
    assert 'database_url' not in source and 'get_secret_value' not in source          # nothing in the tool can print the connection


def test_pilot_fits_the_one_dollar_cap_when_run_one_at_a_time():
    s = gen_settings(listening_formats_enabled=True, listening_format_accents=PILOT_ACCENTS)
    m = c.load_manifest(c.DEFAULT_MANIFEST)
    worst = [gj.reservation_for(s, e.spec)[1] for e in m.entries('pilot')]
    budget = round(m.budgets_usd['pilot'] * 1_000_000)
    observed_settled = 100_000                  # the five existing 2-minute lessons settled at US$0.089-0.108 each (listening_generation_requests, 2026-11)
    assert max(worst) < budget                  # any single lesson can always be reserved
    assert 4 * observed_settled + max(worst) <= budget          # the fifth lesson still fits after four settle at the observed cost
    # and the bound is enforced for real: a stage that has already used almost everything refuses to reserve another
    assert sum(worst) > budget                  # so the check cannot be the naive sum; it is cumulative-settled + one worst case (see build_plan)
