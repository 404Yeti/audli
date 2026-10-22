"""Advisory Review language coaching (AUD-77): gating, idempotency, limits, isolation and state neutrality."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update

from app.config import Settings
from app.improvement import LanguageCoaching, MeaningCheck, Note, source_hash
from app.main import create_app
from app.storage import schema as s
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory  # noqa: F401
from test_auth import accounts_with, USER_A, USER_B  # noqa: F401
from test_review import seed, add_turns

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
ANSWER = 'She change the meeting to Monday because the team was sick'
IMPROVED = 'She changed the meeting to Monday because the team was sick'


def good(text):
    return LanguageCoaching(improved_text=text.replace('She change the', 'She changed the'),
        notes=[Note(kind='grammar', before='She change', after='She changed', why='Use the past tense for a finished action.')],
        meaning_preserved=True, adds_information=False)


class Coach(FakeProvider):
    """Programmable coaching provider that records exactly what it was sent."""
    def __init__(self):
        self.coached, self.checked = [], []
        self.coaching = good
        self.same = True
        self.error = None
        self.delay = 0
        self.during = None

    async def coach_language(self, text):
        self.coached.append(text)
        if self.during:
            self.during()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.coaching(text)

    async def check_meaning(self, original, improved):
        self.checked.append((original, improved))
        return MeaningCheck(same_meaning=self.same)


def set_answer(repo, exercise_id, text=ANSWER, confidence=.95, uncertainty=(), source='openai', recognized=None):
    with repo.transaction() as db:
        db.execute(update(s.attempts).where(s.attempts.c.exercise_id == exercise_id).values(
            transcription={'text': recognized or text, 'confidence': confidence, 'uncertainty': list(uncertainty), 'source': source},
            confirmed_text=text))


class Clock:
    def __init__(self, at=T0):
        self.at = at

    def __call__(self):
        return self.at


def build(tmp_path, repository_factory, **settings):
    repo = repository_factory()
    provider = Coach()
    app = create_app(Settings(data_dir=tmp_path / 'cache', review_improvement_enabled=True, _env_file=None, **settings), provider, repository=repo)
    app.state.review_clock = Clock()
    return app, ASGIClient(app, provider), provider, repo


@pytest.fixture
def env(tmp_path, repository_factory):
    return build(tmp_path, repository_factory)


def seeded(repo, exercise, count=1, text=ANSWER, **kwargs):
    entry, ids = seed(repo, exercise, count=count, **kwargs)
    for exercise_id in ids:
        set_answer(repo, exercise_id, text)
    return entry, ids


def post(client, exercise_id):
    return client.post(f'/api/exercises/{exercise_id}/review/improvement')


def tables(repo):
    names = ('evaluations', 'events', 'profiles', 'lessons', 'attempts', 'turns', 'assessments', 'evidence', 'sessions', 'exercises')
    with repo.engine.connect() as db:
        return {name: [dict(row) for row in db.execute(select(getattr(s, name))).mappings().all()] for name in names}


def rows(repo):
    with repo.engine.connect() as db:
        return [dict(row) for row in db.execute(select(s.review_improvements).order_by(s.review_improvements.c.claimed_at)).mappings().all()]


# ------------------------------------------------------------------------------------------------ flag and gates
def test_flag_defaults_off_and_limits_are_the_approved_values():
    settings = Settings(_env_file=None)
    assert settings.review_improvement_enabled is False
    assert settings.review_improvement_daily_limit == 10


def test_flag_off_has_no_route_and_no_report_field(tmp_path, repository_factory, exercise):
    repo = repository_factory()
    provider = Coach()
    client = ASGIClient(create_app(Settings(data_dir=tmp_path / 'c', _env_file=None), provider, repository=repo), provider)
    entry, ids = seeded(repo, exercise)
    assert post(client, ids[0]).status_code == 404
    assert client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement'] is None
    assert provider.coached == [] and rows(repo) == []


def test_report_offers_coaching_without_calling_a_provider_or_writing(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    before = tables(repo)
    state = client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement']
    assert state['state'] == 'available' and state['improved_text'] is None
    assert provider.coached == [] and provider.checked == [] and rows(repo) == [] and tables(repo) == before


def test_gates_unknown_invalid_unevaluated_and_unsaved(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    assert post(client, str(uuid4())).status_code == 404
    assert post(client, 'not-a-uuid').status_code == 422
    with repo.transaction() as db:
        db.execute(update(s.attempts).where(s.attempts.c.exercise_id == ids[0]).values(status='transcribed'))
    assert post(client, ids[0]).status_code == 403
    assert provider.coached == [] and rows(repo) == []
    with repo.transaction() as db:
        db.execute(update(s.attempts).where(s.attempts.c.exercise_id == ids[0]).values(status='evaluated', confirmed_text=None))
    assert post(client, ids[0]).status_code == 404


@pytest.mark.parametrize('kwargs,reason', [
    (dict(uncertainty=['Speech recognition was uncertain; please verify or record again.']), 'unreliable_recognition'),
    (dict(confidence=.2), 'unreliable_recognition'),
    (dict(confidence=None), 'unreliable_recognition'),
    (dict(text='Short answer here'), 'too_short'),
    (dict(text='word ' * 400), 'too_long'),
])
def test_unreliable_or_unsuitable_answers_never_reach_a_provider(env, exercise, kwargs, reason):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    set_answer(repo, ids[0], **kwargs)
    response = post(client, ids[0])
    assert response.status_code == 200 and response.json()['improvement'] == {**response.json()['improvement'], 'state': 'unavailable', 'reason': reason}
    assert response.json()['improvement']['improved_text'] is None
    report = client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement']
    assert report['state'] == 'unavailable' and report['reason'] == reason
    assert provider.coached == [] and provider.checked == [] and rows(repo) == []


def test_a_learner_corrected_transcript_is_confirmed_even_if_recognition_was_unsure(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    set_answer(repo, ids[0], confidence=.2, uncertainty=['unsure'], recognized='She chains meeting Monday because team was six')
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'


# ------------------------------------------------------------------------------------------------ happy path
def test_ready_result_has_text_notes_and_diff_and_leaves_everything_else_untouched(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    before = tables(repo)
    response = post(client, ids[0])
    state = response.json()['improvement']
    assert response.status_code == 200 and state['state'] == 'ready' and state['improved_text'] == IMPROVED
    assert state['unchanged'] is False and len(state['notes']) == 1 and state['notes'][0]['kind'] == 'grammar'
    assert [(d['op'], d['text'].strip()) for d in state['diff'] if d['op'] != 'equal'] == [('delete', 'change'), ('insert', 'changed')]
    assert ''.join(d['text'] for d in state['diff'] if d['op'] != 'insert') == ANSWER
    assert ''.join(d['text'] for d in state['diff'] if d['op'] != 'delete') == IMPROVED
    # Advisory only: scores, adaptation, evidence, transcript, lessons and profile are byte-identical.
    assert tables(repo) == before
    # The provider saw only the learner's words: never the script, expected answers or exercise content.
    assert provider.coached == [ANSWER] and provider.checked == [(ANSWER, IMPROVED)]
    assert exercise.script not in json.dumps(provider.coached + provider.checked)
    report = client.get(f'/api/review/lesson/{entry}').json()['passages'][0]
    assert report['improvement'] == state and report['answers'][0]['text'] == ANSWER
    assert len(provider.coached) == 1  # Reading the report never regenerates.


def test_already_natural_is_acknowledged_without_inventing_corrections(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise, text='She changed the meeting to Monday because the team was sick')
    provider.coaching = lambda text: LanguageCoaching(improved_text=text, notes=[], meaning_preserved=True, adds_information=False)
    state = post(client, ids[0]).json()['improvement']
    assert state['state'] == 'ready' and state['unchanged'] is True and state['notes'] == [] and state['diff'] == []
    assert provider.checked == []  # Nothing changed, so there is nothing to compare.


def test_older_records_without_turns_use_the_evaluated_response(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    add_turns(repo, ids[0], [(ANSWER, None, []), ('It was Monday', 'When was it?', [])])
    set_answer(repo, ids[0])
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'
    assert provider.coached == [ANSWER]  # The summary only, not the follow-up.


# ------------------------------------------------------------------------------------------------ rejection
@pytest.mark.parametrize('make,code', [
    (lambda t: LanguageCoaching(improved_text="She didn't change the meeting to Monday because the team was sick",
        notes=[Note(kind='grammar', before='She change', after="She didn't change", why='x')], meaning_preserved=True, adds_information=False), 'negation_changed'),
    (lambda t: LanguageCoaching(improved_text=IMPROVED, notes=[Note(kind='grammar', before='She change', after='She changed', why='x')],
        meaning_preserved=False, adds_information=False), 'meaning_not_preserved'),
    (lambda t: LanguageCoaching(improved_text=IMPROVED, notes=[Note(kind='grammar', before='She change', after='She changed', why='x')],
        meaning_preserved=True, adds_information=True), 'information_added'),
    (lambda t: LanguageCoaching(improved_text=IMPROVED, notes=[], meaning_preserved=True, adds_information=False), 'unexplained_changes'),
])
def test_meaning_changing_or_unexplained_results_are_rejected_and_never_shown(env, exercise, make, code):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    provider.coaching = make
    response = post(client, ids[0])
    state = response.json()['improvement']
    assert response.status_code == 200 and state['state'] == 'rejected' and state['reason'] == code
    assert state['improved_text'] is None and state['notes'] == [] and state['diff'] == []
    assert 'didn' not in response.text and IMPROVED not in response.text
    stored = rows(repo)
    assert [r['status'] for r in stored] == ['rejected'] and stored[0]['improved_text'] is None
    assert provider.checked == []  # Rejected by the deterministic layers before the second provider call.
    again = post(client, ids[0])
    assert again.json()['improvement']['state'] == 'rejected' and len(provider.coached) == 1  # Terminal: no retry, no quota.


def test_independent_meaning_check_rejects_what_the_lexical_layers_cannot_see(env, exercise):
    """A noun swapped through a 'vocabulary' note passes every deterministic class, so the second provider decides."""
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise, text='The team fixed the database problem fast yesterday')
    provider.coaching = lambda t: LanguageCoaching(improved_text='The team repaired the network problem fast yesterday',
        notes=[Note(kind='vocabulary', before='fixed the database', after='repaired the network', why='A more precise verb.')],
        meaning_preserved=True, adds_information=False)
    provider.same = False
    state = post(client, ids[0]).json()['improvement']
    assert state['state'] == 'rejected' and state['reason'] == 'meaning_check_failed' and state['improved_text'] is None
    assert len(provider.checked) == 1


def test_original_transcript_is_never_modified(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    with repo.engine.connect() as db:
        before = [dict(r) for r in db.execute(select(s.attempts).where(s.attempts.c.exercise_id == ids[0])).mappings()]
    post(client, ids[0])
    with repo.engine.connect() as db:
        assert [dict(r) for r in db.execute(select(s.attempts).where(s.attempts.c.exercise_id == ids[0])).mappings()] == before
    assert client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['answers'][0]['text'] == ANSWER


# ------------------------------------------------------------------------------------------------ idempotency/concurrency
def test_repeating_the_request_is_idempotent_and_free(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    first = post(client, ids[0]).json()
    for _ in range(3):
        assert post(client, ids[0]).json() == first
    assert len(provider.coached) == 1 and len(rows(repo)) == 1


def test_concurrent_requests_make_one_provider_call(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise)
    provider.delay = .1

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*[http.post(f'/api/exercises/{ids[0]}/review/improvement') for _ in range(6)])
    responses = asyncio.run(run())
    assert sorted({r.status_code for r in responses}) in ([200], [200, 202])
    assert len(provider.coached) == 1 and len(rows(repo)) == 1
    assert [r.json()['improvement']['state'] for r in responses].count('ready') >= 1
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'


def test_concurrent_claims_across_exercises_cannot_exceed_the_allowance(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=3, review_improvement_max_concurrent=8)
    entry, ids = seeded(repo, exercise, count=8)
    provider.delay = .05

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*[http.post(f'/api/exercises/{i}/review/improvement') for i in ids])
    responses = asyncio.run(run())
    assert sorted(r.status_code for r in responses).count(429) == 5 and len(provider.coached) == 3


def test_provider_calls_are_bounded_by_concurrency_slots(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_max_concurrent=1)
    entry, ids = seeded(repo, exercise, count=3)
    provider.delay = .15

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            return await asyncio.gather(*[http.post(f'/api/exercises/{i}/review/improvement') for i in ids])
    codes = sorted(r.status_code for r in asyncio.run(run()))
    assert codes == [200, 503, 503] and len(provider.coached) == 1
    # A busy refusal frees its claim instead of consuming the allowance, so the others can succeed on retry.
    assert [r['status'] for r in rows(repo)].count('ready') == 1
    assert all(post(client, i).status_code == 200 for i in ids)


# ------------------------------------------------------------------------------------------------ allowance
def test_ten_new_attempts_per_rolling_day_and_reads_are_free(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise, count=11)
    clock = app.state.review_clock
    for index, exercise_id in enumerate(ids[:10]):
        clock.at = T0 + timedelta(minutes=index)
        assert post(client, exercise_id).status_code == 200, index
    clock.at = T0 + timedelta(hours=1)
    blocked = post(client, ids[10])
    assert blocked.status_code == 429 and blocked.json()['improvement']['code'] == 'improvement_limit'
    wait = blocked.json()['improvement']['retry_after_seconds']
    assert blocked.headers['retry-after'] == str(wait) and wait == 23 * 3600 + 1  # The first claim leaves the window at T0+24h.
    assert 'language suggestions' in blocked.json()['detail'] and len(provider.coached) == 10
    # Reopening, replaying and report reads never consume or require allowance.
    for exercise_id in ids[:10]:
        assert post(client, exercise_id).json()['improvement']['state'] == 'ready'
    assert client.get(f'/api/review/lesson/{entry}').status_code == 200
    assert len(provider.coached) == 10
    clock.at = T0 + timedelta(hours=24, seconds=2)
    assert post(client, ids[10]).status_code == 200


def test_failed_attempts_do_not_consume_the_allowance_and_retry_is_throttled(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=2)
    entry, ids = seeded(repo, exercise, count=4)
    clock = app.state.review_clock
    provider.error = ValueError('SECRET learner text and provider payload')
    first = post(client, ids[0])
    assert first.status_code == 503 and 'SECRET' not in first.text
    assert [r['status'] for r in rows(repo)] == ['failed']
    throttled = post(client, ids[0])
    assert throttled.status_code == 429 and throttled.json()['improvement']['code'] == 'improvement_cooldown'
    assert len(provider.coached) == 1
    provider.error = None
    clock.at += timedelta(seconds=31)
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'
    assert post(client, ids[1]).json()['improvement']['state'] == 'ready'
    assert post(client, ids[2]).status_code == 429  # Two counted attempts; the failure was never counted.


def test_repeated_failures_are_bounded_and_recover(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory)
    entry, ids = seeded(repo, exercise, count=7)
    clock = app.state.review_clock
    provider.error = TimeoutError()
    for exercise_id in ids[:5]:
        assert post(client, exercise_id).status_code == 503
    burst = post(client, ids[5])
    assert burst.status_code == 429 and burst.json()['improvement']['code'] == 'improvement_unavailable' and len(provider.coached) == 5
    provider.error = None
    clock.at += timedelta(hours=1, seconds=5)
    assert post(client, ids[5]).json()['improvement']['state'] == 'ready'


def insert_pending(repo, exercise_id, claimed_at, status='pending'):
    with repo.engine.connect() as db:
        attempt = db.execute(select(s.attempts.c.id).where(s.attempts.c.exercise_id == exercise_id)).scalar_one()
    with repo.transaction() as db:
        db.execute(s.review_improvements.insert().values(id=str(uuid4()), user_id=repo.learner_id, exercise_id=exercise_id,
            attempt_id=attempt, status=status, source_hash=source_hash(ANSWER), prompt_version='v1', claimed_at=claimed_at))


def test_abandoned_claims_never_exhaust_the_allowance(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=2)
    entry, ids = seeded(repo, exercise, count=4)
    # Two claims whose workers vanished minutes ago must not hold the learner's two slots.
    insert_pending(repo, ids[0], T0 - timedelta(minutes=10))
    insert_pending(repo, ids[1], T0 - timedelta(minutes=5))
    assert post(client, ids[2]).json()['improvement']['state'] == 'ready'
    assert post(client, ids[3]).json()['improvement']['state'] == 'ready'
    # An abandoned claim on the same exercise is superseded and the work completes.
    assert post(client, ids[0]).status_code == 429  # Now the two real results fill the allowance...
    app.state.review_clock.at = T0 + timedelta(hours=25)
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'  # ...and it recovers when the window moves.
    assert 'abandoned' in [r['status'] for r in rows(repo)]


def test_a_fresh_pending_claim_is_joined_not_duplicated(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    insert_pending(repo, ids[0], T0 - timedelta(seconds=10))
    response = post(client, ids[0])
    assert response.status_code == 202 and response.json()['improvement']['state'] == 'pending' and provider.coached == []
    assert client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement']['state'] == 'pending'
    app.state.review_clock.at = T0 + timedelta(minutes=5)  # The lease has expired: it was abandoned.
    assert client.get(f'/api/review/lesson/{entry}').json()['passages'][0]['improvement']['state'] == 'available'
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'


def test_a_cancelled_request_abandons_its_claim_without_consuming_allowance(tmp_path, repository_factory, exercise):
    app, client, provider, repo = build(tmp_path, repository_factory, review_improvement_daily_limit=1)
    entry, ids = seeded(repo, exercise, count=2)
    provider.delay = 5

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            task = asyncio.create_task(http.post(f'/api/exercises/{ids[0]}/review/improvement'))
            for _ in range(100):
                await asyncio.sleep(.02)
                if provider.coached:
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
    assert [r['status'] for r in rows(repo)] == ['abandoned']
    provider.delay = 0
    assert post(client, ids[1]).json()['improvement']['state'] == 'ready'  # The single slot was not consumed.


# ------------------------------------------------------------------------------------------------ locks and AUD-47
def test_no_database_transaction_or_lock_is_held_while_the_provider_runs(env, exercise):
    app, client, provider, repo = env
    entry, ids = seeded(repo, exercise)
    probes = []

    def during():
        started = time.monotonic()
        with repo.transaction() as db:  # BEGIN IMMEDIATE on SQLite: blocks (20 s busy timeout) if a write lock is held.
            db.execute(select(s.users.c.id).limit(1)).all()
        probes.append(time.monotonic() - started)
    provider.during = during
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'
    assert probes and probes[0] < 1.5


def test_the_daily_lesson_allowance_is_never_consulted_or_changed(tmp_path, repository_factory, exercise):
    repo = repository_factory()
    provider = Coach()
    app = create_app(Settings(data_dir=tmp_path / 'c', review_improvement_enabled=True, lesson_limit_mode='enforce', _env_file=None), provider, repository=repo)
    app.state.review_clock = Clock()

    def forbidden(*args, **kwargs):
        raise AssertionError('Review coaching must not consult the lesson allowance')
    for name in ('admit', 'admit_exercise', 'admit_legacy', 'block_onboarding_restart'):
        setattr(app.state.limits, name, forbidden)
    entry, ids = seeded(repo, exercise)
    with repo.transaction() as db:  # A learner in cooldown, with no open lesson, must still be able to review.
        db.execute(s.lesson_entitlements.insert().values(user_id=repo.learner_id, last_started_at=T0, next_eligible_at=T0 + timedelta(hours=24),
            last_lesson_id=entry, first_speech_failures=0, grace_actions=0, updated_at=T0))
    before = [dict(r) for r in repo.engine.connect().execute(select(s.lesson_entitlements)).mappings()]
    client = ASGIClient(app, provider)
    assert post(client, ids[0]).json()['improvement']['state'] == 'ready'
    assert [dict(r) for r in repo.engine.connect().execute(select(s.lesson_entitlements)).mappings()] == before
    assert client.get('/api/review').status_code == 200


# ------------------------------------------------------------------------------------------------ isolation
@pytest.fixture
def accounts_on(tmp_path, repository_factory):
    yield from accounts_with(tmp_path, repository_factory, Coach, review_improvement_enabled=True)


def test_cross_account_isolation_and_independent_allowances(accounts_on, exercise):
    a, b, _ = accounts_on
    a.get('/api/profile'); b.get('/api/profile')
    clock = Clock()
    a.app.state.review_clock = clock
    owner, other = a.app.state.repository.for_learner(USER_A), a.app.state.repository.for_learner(USER_B)
    a_entry, a_ids = seeded(owner, exercise)
    b_entry, b_ids = seeded(other, exercise, text='He go to office by bus every day')
    mine = post(a, a_ids[0])
    assert mine.json()['improvement']['state'] == 'ready'
    # Another learner cannot reach, trigger, or see it.
    assert post(b, a_ids[0]).status_code == 404
    assert a.app.state.repository.for_learner(USER_B).engine is not None
    for path in (f'/api/review/lesson/{a_entry}', '/api/review'):
        assert b.get(path).status_code in (200, 404) and IMPROVED not in b.get(path).text and ANSWER not in b.get(path).text
    assert ASGIClient(a.app, a.provider).post(f'/api/exercises/{a_ids[0]}/review/improvement').status_code == 401
    assert a.get(f'/api/review/lesson/{a_entry}').json()['passages'][0]['improvement']['improved_text'] == IMPROVED
    # Quotas are per learner.
    a.provider.coaching = lambda text: LanguageCoaching(improved_text=text, notes=[], meaning_preserved=True, adds_information=False)
    assert post(b, b_ids[0]).json()['improvement']['state'] == 'ready'
    assert {r['user_id'] for r in rows(owner)} == {USER_A, USER_B}
    assert a.provider.coached == [ANSWER, 'He go to office by bus every day']


def test_foreign_exercise_id_cannot_be_claimed_even_with_a_valid_own_session(accounts_on, exercise):
    a, b, _ = accounts_on
    a.get('/api/profile'); b.get('/api/profile')
    owner = a.app.state.repository.for_learner(USER_A)
    _, ids = seeded(owner, exercise)
    assert post(b, ids[0]).status_code == 404
    assert rows(owner) == [] and a.provider.coached == []
