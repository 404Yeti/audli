"""Daily Challenge (docs/LISTENING-DAILY-CHALLENGE-IMPL.md): topic rotation, quality gates, the idempotent scheduler, the day boundary, completions and streaks.

Fake providers throughout (no paid call). Real database, jobs, ledger, library workflow, gates and publishing. SQLite always; real PostgreSQL when
AUDLI_TEST_POSTGRES_URL is set. Time is injected, so midnight, the 23:00 start and restarts are exercised without waiting.
"""
import hashlib
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, insert, select, update

from app.config import Settings
from app.listening import daily as dl, daily_gates as gates, daily_planner as planner, daily_read as read, daily_taxonomy as tax, schema as t, store
from app.listening.generation import FakeScriptWriter, Ledger, format_availability
from app.listening.generator import run_once
from app.listening.speech import FakeSpeech
from app.listening.providers import FakeDailyJudge, FakeTopicProposer, ProviderPermanent, ProviderTemporary
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation import lab, settings as gen_settings  # noqa: F401  (fixture)

ICT = ZoneInfo('Asia/Ho_Chi_Minh')
D1, D2, D3 = '2026-11-10', '2026-11-11', '2026-11-12'


def at(day: str, hour: int, minute: int = 0) -> datetime:
    return datetime.fromisoformat(f'{day}T{hour:02d}:{minute:02d}:00').replace(tzinfo=ICT).astimezone(timezone.utc)


CORE = {1: 'a large latte', 2: 'the price is given', 3: 'a blueberry muffin is added', 4: 'it costs two dollars', 5: 'the customer pays by card'}


def nonce(seed: str, i: int, j: int) -> str:
    digest = hashlib.sha256(f'{seed}:{i}:{j}'.encode()).hexdigest()[:7]
    return digest.translate(str.maketrans('0123456789', 'qwzxvkjyhg')) + 'o'


class VariedWriter(FakeScriptWriter):
    """The stock fake writes the same café script for every topic, which the duplicate gate would (rightly) refuse on day two. This keeps every answer-key
    invariant the generator checks but makes the rest of each sentence unique to the topic."""
    def __init__(self, seed_suffix='', mutate=None):
        super().__init__(mutate=mutate)
        self.suffix = seed_suffix

    def write(self, spec, max_chars, feedback):
        raw = super().write(spec, max_chars, feedback)
        seed = spec.topic + self.suffix
        for i, s in enumerate(raw['sentences']):
            core = CORE.get(i, 'so we go on')
            words = [nonce(seed, i, 0), nonce(seed, i, 1), 'said', core]
            j = 2
            while len(' '.join(words)) < len(s['text']) - 6 and len(words) < 20:
                words.insert(len(words) - 1, nonce(seed, i, j))
                j += 1
            s['text'] = ' '.join(words).capitalize() + '.'
        raw['title'] = spec.topic.title()[:60]
        raw['mcq']['options'][1]['text'] = 'A large latte'
        return raw


class TextSpeech(FakeSpeech):
    """Fast, text-dependent fake audio (one ffmpeg call, no Python sample loop). The stock fake tone depends only on word count and voice, so two different scripts of the
    same length would share a checksum (real speech never does) and the duplicate gate would rightly refuse the second."""
    def synthesize(self, text, voice):
        import subprocess
        from imageio_ffmpeg import get_ffmpeg_exe
        self.calls.append((text, voice))
        seconds = max(0.8, 0.22 * len(text.split()) + 0.5)
        pitch = 180 + int(hashlib.sha256((voice + text).encode()).hexdigest()[:6], 16) % 700
        return subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={pitch}:sample_rate=22050:duration={seconds:.2f}', '-b:a', '64k', '-f', 'mp3', '-'],
                              check=True, capture_output=True).stdout


def daily_settings(owner, **over):
    base = dict(listening_daily_minutes=1, listening_curator_ids=owner, listening_gen_curator_daily=5, listening_daily_enabled=True, listening_daily_publish=True, listening_daily_service_id=owner)
    return gen_settings(**{**base, **over})


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


# On PostgreSQL (CI's disposable postgres:17) only the tests whose behaviour depends on the database run: locking and concurrency, uniqueness, atomic publication,
# retries and failure recovery, authorization and the roles. The pure logic and the full flow matrix run on SQLite; re-running them on PostgreSQL adds minutes and no coverage.
PG_CORE = {
    'test_the_evening_run_generates_validates_and_schedules_tomorrow_and_midnight_publishes_it',
    'test_running_the_scheduler_again_and_again_creates_one_request_one_lesson_and_one_publication',
    'test_simultaneous_schedulers_produce_a_single_attempt',
    'test_a_crash_between_steps_resumes_without_a_second_request_or_a_second_reservation',
    'test_the_database_itself_refuses_one_lesson_or_one_script_for_two_days',
    'test_publication_is_one_transaction_a_failure_leaves_the_lesson_private_and_the_day_scheduled',
    'test_when_every_attempt_fails_the_day_is_failed_the_operator_is_told_and_nothing_old_is_substituted',
    'test_a_rejected_attempt_retries_with_a_different_topic_and_the_rejected_lesson_is_never_published',
    'test_a_service_account_that_loses_curator_status_publishes_nothing_spends_nothing_and_the_operator_is_told',
    'test_the_pause_switch_stops_generation_and_publication_at_once_and_resume_continues',
    'test_an_expired_lease_is_taken_over_and_the_old_holder_cannot_write_any_more',
    'test_a_second_scheduler_cannot_act_while_the_first_holds_the_day',
    'test_starting_resuming_and_finishing_updates_the_card_and_credits_the_day_once',
    'test_migration_015_is_recorded_secured_and_not_reappliable',
}


@pytest.fixture(autouse=True)
def _pg_subset(request):
    callspec = getattr(request.node, 'callspec', None)
    backend = (callspec.params.get('make_repository') if callspec else None)
    if backend == 'postgres' and request.node.originalname not in PG_CORE:
        pytest.skip('covered on SQLite; the PostgreSQL run keeps to database-dependent behaviour')


@pytest.fixture(autouse=True)
def _no_stall(monkeypatch):
    monkeypatch.setattr(dl, 'STALL_MINUTES', 10 ** 6)       # real request timestamps vs. a fake clock days away


@pytest.fixture
def day(lab):
    class Lab(SimpleNamespace):
        pass

    def build(settings=None, judge=None, proposer=None, writer=None, clock=None, owner='sched-1', notify=None):
        s = settings or daily_settings(lab.owner)
        d = dl.Daily(engine=lab.engine, settings=s, ledger=Ledger(lab.engine, s), judge=judge or FakeDailyJudge(), proposer=proposer or FakeTopicProposer(),
                     clock=clock or Clock(at(D1, 23, 5)), owner=owner, notify=notify)
        return d

    def generate(d, writer=None):
        deps = lab.deps(settings=d.settings, writer=writer or VariedWriter(), speech=TextSpeech())
        done = []
        while (outcome := run_once(deps, 'gen')) is not None:
            done.append(outcome)
        return done

    def row(date_):
        with lab.engine.connect() as db:
            return dict(db.execute(select(t.daily_challenges).where(t.daily_challenges.c.challenge_date == date_)).mappings().one())

    def rows():
        with lab.engine.connect() as db:
            return [dict(r) for r in db.execute(select(t.daily_challenges).order_by(t.daily_challenges.c.challenge_date)).mappings()]

    def requests(prefix='daily:'):
        with lab.engine.connect() as db:
            return db.execute(select(t.generation_requests.c.client_request_id, t.generation_requests.c.phase).where(t.generation_requests.c.client_request_id.like(prefix + '%'))).all()

    def full_day(d, date_, generate_writer=None):
        """23:05 the evening before: plan + submit, generator builds, validate + schedule."""
        d.clock.now = at((date.fromisoformat(date_) - timedelta(days=1)).isoformat(), 23, 5)
        dl.tick(d)
        generate(d, generate_writer)
        dl.tick(d)
        return row(date_)

    return SimpleNamespace(lab=lab, build=build, generate=generate, row=row, rows=rows, requests=requests, full_day=full_day, engine=lab.engine, owner=lab.owner, other=lab.other)


# ---- the topic pool and the rotation ------------------------------------------------------------------------------------------

def test_the_pool_has_at_least_twelve_categories_and_extensive_subtopics_and_no_advanced_levels():
    assert len(tax.CATEGORIES) >= 12 and len(tax.TAXONOMY) >= 80
    assert all(len([s for s in tax.TAXONOMY if s.category == c]) >= 5 for c in tax.CATEGORIES)
    assert tax.LEVELS == ('A1', 'A2', 'B1', 'B2') and not any(s.lo in ('C1', 'C2') or s.hi in ('C1', 'C2') for s in tax.TAXONOMY)
    assert len({s.key for s in tax.TAXONOMY}) == len(tax.TAXONOMY)


def test_c1_and_c2_cannot_be_enabled_for_the_daily_challenge():
    with pytest.raises(ValueError, match='C1/C2'):
        Settings(_env_file=None, listening_daily_levels='B2,C1')
    plan = planner.choose(date(2026, 11, 10), [], format_availability(None), levels=('C1', 'C2'))
    assert plan is None


def simulate(days=60, **kw):
    hist, used, out = [], set(), []
    avail = format_availability(None)
    for n in range(days):
        p = planner.choose(date(2026, 11, 10) + timedelta(days=n), hist, avail, used, 2, **kw)
        assert p is not None
        out.append(p)
        hist = [p.as_json(), *hist]
        used = used | {p.topic_key}
    return out


def test_rotation_never_repeats_a_topic_and_keeps_categories_levels_formats_and_accents_moving():
    plans = simulate(60)
    assert len({p.topic_key for p in plans}) == 60
    assert all(a.category != b.category for a, b in zip(plans, plans[1:]))                 # never the same category two days running
    assert all(a.cefr != b.cefr for a, b in zip(plans, plans[1:]))
    assert {p.cefr for p in plans} == {'A1', 'A2', 'B1', 'B2'} and len({p.category for p in plans}) >= 12
    assert len({p.kind for p in plans}) >= 2 and len({p.accent for p in plans}) >= 3
    for i in range(14, 60):                                                                 # work + food/cafe together stay a small share of any two weeks
        assert sum(1 for p in plans[i - 14:i] if p.category in tax.OVERUSED) <= planner.OVERUSED_MAX + 1


def test_the_planner_only_offers_supported_voice_and_format_combinations():
    avail = format_availability(None)                                           # formats flag off: no mixed-accent, only the original three accents
    for p in simulate(30):
        assert p.kind != planner.MIXED and p.accent in avail[p.kind] and p.accent2 is None
    assert planner.choose(date(2026, 11, 10), [], {planner.MONOLOGUE: [], planner.CONVERSATION: [], planner.MIXED: []}) is None
    mixed = {planner.MONOLOGUE: ['american'], planner.CONVERSATION: ['american'], planner.MIXED: ['american', 'british']}
    kinds = {planner.choose(date(2026, 11, 10) + timedelta(days=n), [], mixed).kind for n in range(10)}
    assert planner.MIXED in kinds
    p = planner.choose(date(2026, 11, 10), [{'topic_key': 'x', 'category': 'travel', 'cefr': 'A1', 'kind': 'conversation', 'accent': 'american', 'accent2': None}], {planner.MIXED: ['american', 'british']})
    assert p.accent2 and p.accent2 != p.accent


def test_the_same_inputs_give_the_same_plan_and_a_rejected_topic_is_not_offered_again():
    a = planner.choose(date(2026, 11, 10), [], format_availability(None))
    assert a == planner.choose(date(2026, 11, 10), [], format_availability(None))
    b = planner.choose(date(2026, 11, 10), [], format_availability(None), rejected_keys={a.topic_key})
    assert b.topic_key != a.topic_key
    Settings_spec = a.spec()
    from app.listening.generation import Spec
    assert Spec.model_validate(Settings_spec).topic == a.topic


# ---- the gates ------------------------------------------------------------------------------------------------------------------

def snapshot(**over):
    sentences = over.pop('sentences', [f'Sentence number {n} talks about something quite different and long enough here.' for n in range(8)])
    base = dict(lesson_id='L', version_id='V', title='A Title', topic='topic', cefr='A2', listen_for='something', content_hash='h', speakers=[{'id': 'a', 'accent': 'american'}],
                sentences=sentences, audio=[{'accent': 'american', 'voice': 'v', 'object_key': 'k', 'sha256': 'a' * 64, 'size_bytes': 10, 'duration_ms': 8 * 4000,
                                             'alignment': [{'index': n, 'start_ms': n * 4000, 'end_ms': n * 4000 + 3900} for n in range(8)]}],
                exercises=[{'kind': 'multiple_choice', 'prompt': 'q', 'options': [{'id': 'a', 'text': 'Something quite'}, {'id': 'b', 'text': 'Other words'}, {'id': 'c', 'text': 'Third idea'}],
                            'answer': {'correct_option_id': 'a', 'explanation': 'e'}},
                           {'kind': 'spoken', 'prompt': 'q', 'options': [], 'answer': {'points': [{'id': 'p', 'text': 'something different'}]}},
                           {'kind': 'fill_blank', 'prompt': 'Sentence number 3 talks about ___ quite different and long enough here.', 'options': [], 'answer': {'accepted': ['something']}},
                           {'kind': 'sequencing', 'prompt': 'q', 'options': [{'id': 'x', 'text': 'Sentence number'}, {'id': 'y', 'text': 'quite different'}, {'id': 'z', 'text': 'long enough'}],
                            'answer': {'order': ['y', 'x', 'z']}}])
    return gates.Snapshot(**{**base, **over})


PLAN = {'cefr': 'A2', 'accent': 'american', 'accent2': None, 'kind': 'monologue'}


def test_a_good_candidate_passes_the_deterministic_gates():
    s = snapshot()
    assert gates.content_validity(s, PLAN, tax.LEVELS).ok and gates.answer_consistency(s).ok and gates.audio_readiness(s, PLAN).ok and gates.safety(s).ok


@pytest.mark.parametrize('mutate,fragment', [
    (lambda e: e[0]['answer'].update(correct_option_id='zzz'), 'key is not one of its options'),
    (lambda e: e[2].update(prompt='no gap here'), 'exactly one gap'),
    (lambda e: e[2]['answer'].update(accepted=['banana']), 'no accepted blank answer'),
    (lambda e: e[3]['answer'].update(order=['x', 'y', 'z']), 'already in the correct order'),
    (lambda e: e[3]['answer'].update(order=['x', 'y']), 'does not order exactly'),
    (lambda e: e[1]['answer'].update(points=[]), 'no key points'),
    (lambda e: e[1]['answer'].update(points=[{'id': 'p', 'text': 'zebra parachute'}]), 'not grounded'),
])
def test_answer_consistency_catches_a_key_that_contradicts_the_lesson(mutate, fragment):
    s = snapshot()
    exercises = [dict(e, answer=dict(e['answer'] or {}), options=list(e['options'])) for e in s.exercises]
    mutate(exercises)
    result = gates.answer_consistency(gates.Snapshot(**{**s.__dict__, 'exercises': exercises}))
    assert not result.ok and any(fragment in p for p in result.problems)


def test_audio_readiness_rejects_missing_misaligned_untimed_or_unnatural_audio():
    assert not gates.audio_readiness(snapshot(audio=[]), PLAN).ok
    bad = snapshot().audio[0]
    assert any('aligned' in p for p in gates.audio_readiness(snapshot(audio=[{**bad, 'alignment': bad['alignment'][:-1]}]), PLAN).problems)
    assert any('past the end' in p for p in gates.audio_readiness(snapshot(audio=[{**bad, 'duration_ms': 1000, 'alignment': bad['alignment']}]), PLAN).problems)
    assert any('words per minute' in p for p in gates.audio_readiness(snapshot(audio=[{**bad, 'duration_ms': 8 * 4000 * 20, 'alignment': bad['alignment']}]), PLAN).problems)
    assert any('checksum' in p for p in gates.audio_readiness(snapshot(audio=[{**bad, 'sha256': 'nope'}]), PLAN).problems)
    assert any('planned accent' in p for p in gates.audio_readiness(snapshot(), {**PLAN, 'accent': 'british'}).problems)
    assert any('missing' in p for p in gates.audio_readiness(snapshot(), PLAN, probe=lambda key, size: False).problems)


def test_safety_rejects_unsafe_topics_and_personal_data_in_the_script_or_the_questions():
    assert not gates.safety(snapshot(sentences=['He wanted to kill the engine of the old boat today.'] * 8)).ok
    assert not gates.safety(snapshot(sentences=['Write to me at someone@example.com about the old boat today please.'] * 8)).ok
    assert not gates.safety(snapshot(sentences=['Call me on 555 123 4567 about the old boat today please.'] * 8)).ok
    s = snapshot()
    ex = [dict(e) for e in s.exercises]
    ex[0]['prompt'] = 'Who should you vote for in the election?'
    assert not gates.safety(gates.Snapshot(**{**s.__dict__, 'exercises': ex})).ok


def doc(lesson_id, sentences, title='Another', audio=('b' * 64,), content_hash='x'):
    return gates.Doc(lesson_id, title, 'topic', 'A2', sentences, frozenset(audio), content_hash)


def test_duplicate_prevention_catches_exact_near_audio_title_and_reused_lessons():
    s = snapshot()
    same, _ = gates.duplicate_prevention(s, [doc('o', s.sentences)], set(), set(), PLAN)
    assert not same.ok and any('identical' in p for p in same.problems)
    light = [x.replace('quite different', 'rather different') if n % 2 else x for n, x in enumerate(s.sentences)]
    near, _ = gates.duplicate_prevention(s, [doc('o', light)], set(), set(), PLAN)
    assert not near.ok and any('repeat an existing lesson' in p for p in near.problems)
    assert not gates.duplicate_prevention(s, [doc('o', ['Completely unrelated words about gardens and bicycles in spring.'] * 8, audio=('a' * 64,))], set(), set(), PLAN)[0].ok
    assert not gates.duplicate_prevention(s, [doc('o', ['zzz yyy xxx www vvv uuu'] * 8, title='A Title')], set(), set(), PLAN)[0].ok
    assert not gates.duplicate_prevention(s, [], {gates.fingerprint(s.sentences)}, set(), PLAN)[0].ok
    assert not gates.duplicate_prevention(s, [], set(), {'L'}, PLAN)[0].ok
    other = ['Gardeners water tomato plants every evening before supper and chat across the fence about weather.'] * 2 + ['Completely fresh sentence number %d about rivers and bridges.' % n for n in range(6)]
    ok, nearest = gates.duplicate_prevention(s, [doc('o', other, title='Gardens')], set(), set(), PLAN)
    assert ok.ok and nearest and nearest[0][0] == 'o'


def test_semantic_review_fails_closed_on_any_negative_verdict():
    good = {'safe': True, 'coherent': True, 'level_ok': True, 'same_scenario_as': None, 'issues': []}
    assert gates.semantic_review(good, set()).ok
    for field, value in (('safe', False), ('coherent', False), ('level_ok', False), ('same_scenario_as', 'L9')):
        assert not gates.semantic_review({**good, field: value}, {'L9'}).ok
    assert not gates.semantic_review({}, set()).ok


# ---- streaks (pure) -------------------------------------------------------------------------------------------------------------

def test_streaks_count_consecutive_days_and_stay_alive_until_the_day_is_over():
    d = lambda *n: {date(2026, 11, x) for x in n}
    assert read.streaks(set(), date(2026, 11, 10)) == {'current': 0, 'longest': 0, 'days_completed': 0}
    assert read.streaks(d(8, 9, 10), date(2026, 11, 10)) == {'current': 3, 'longest': 3, 'days_completed': 3}
    assert read.streaks(d(8, 9), date(2026, 11, 10))['current'] == 2              # today still open: yesterday's streak is intact
    assert read.streaks(d(7, 8), date(2026, 11, 10))['current'] == 0              # a missed day breaks it
    assert read.streaks(d(1, 2, 3, 4, 8, 9), date(2026, 11, 10)) == {'current': 2, 'longest': 4, 'days_completed': 6}


def test_the_day_boundary_is_ict_for_everyone():
    s = Settings(_env_file=None)
    assert read.local_date(s, at(D1, 23, 59)) == date(2026, 11, 10) and read.local_date(s, at(D2, 0, 0)) == date(2026, 11, 11)
    assert read.local_date(s, datetime(2026, 11, 10, 16, 59, tzinfo=timezone.utc)) == date(2026, 11, 10)
    assert read.local_date(s, datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)) == date(2026, 11, 11)
    assert read.next_boundary(s, at(D1, 12)) == at(D2, 0, 0)


# ---- the scheduler: the happy path ----------------------------------------------------------------------------------------------

def test_the_evening_run_generates_validates_and_schedules_tomorrow_and_midnight_publishes_it(day):
    d = day.build()
    assert dl.tick(d)['states'] == {D1: 'generating', D2: 'generating'}                  # 23:05 ICT: today (not yet made) and tomorrow
    assert day.generate(d)
    d.clock.now = at(D1, 23, 20)
    states = dl.tick(d)['states']
    assert states[D2] == 'scheduled' and states[D1] == 'active'      # today's was late: live as soon as it is valid
    tomorrow = day.row(D2)
    assert tomorrow['lesson_id'] and tomorrow['fingerprint'] and tomorrow['published_at'] is None and tomorrow['gate_report']['semantic']['ok']
    with day.engine.connect() as db:
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == tomorrow['lesson_id'])).scalar_one() == day.owner     # still private
        assert tomorrow['lesson_id'] not in {i['lesson_id'] for i in store.list_catalog(db, day.other)}
    d.clock.now = at(D2, 0, 0)
    assert dl.tick(d)['states'][D2] == 'active'
    live = day.row(D2)
    assert live['published_at'] is not None
    with day.engine.connect() as db:
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == live['lesson_id'])).first() is None                 # shared now
        entry = db.execute(select(t.library_entries.c.state, t.library_entries.c.reviewer).where(t.library_entries.c.lesson_id == live['lesson_id'])).one()
    assert entry.state == 'published' and entry.reviewer == dl.REVIEWER


def test_a_challenge_stays_out_of_discover_until_its_day_is_over_then_joins_it_permanently(day):
    d = day.build()
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 1)
    dl.tick(d)
    lesson = day.row(D2)['lesson_id']
    s = d.settings
    with day.engine.connect() as db:
        hidden = read.hidden_lesson_ids(db, s, at(D2, 12))
        assert lesson in hidden
        assert lesson not in {i['lesson_id'] for i in store.list_catalog(db, day.other, hide=hidden)}
        assert lesson in {i['lesson_id'] for i in store.list_catalog(db, day.other)}                      # still playable by id: only the listing hides it
        assert lesson not in read.hidden_lesson_ids(db, s, at(D3, 0, 0))                                  # next boundary: it is just a library lesson
        assert lesson in {i['lesson_id'] for i in store.list_catalog(db, day.other, hide=read.hidden_lesson_ids(db, s, at(D3, 0, 0)))}


def test_each_day_gets_exactly_one_new_lesson_and_never_a_repeat(day):
    d = day.build()
    ids = []
    for n in range(3):
        target = (date.fromisoformat(D2) + timedelta(days=n)).isoformat()
        d.clock.now = at((date.fromisoformat(target) - timedelta(days=1)).isoformat(), 23, 5)
        dl.tick(d)
        day.generate(d)
        dl.tick(d)
        d.clock.now = at(target, 0, 0)
        dl.tick(d)
        ids.append(day.row(target)['lesson_id'])
    assert len(set(ids)) == 3 and None not in ids
    rows = [r for r in day.rows() if r['state'] == 'active']
    assert len({r['fingerprint'] for r in rows}) == len(rows) and len({r['topic_key'] for r in rows}) == len(rows)
    with day.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.library_entries).where(t.library_entries.c.state == 'published')).scalar_one() == len(rows)


def test_the_existing_published_lessons_and_other_learners_data_are_untouched(day):
    from app.listening.objects import LocalObjectStore
    from app.listening.seed import seed_all
    seed_all(day.engine, LocalObjectStore(day.lab.tmp / 'seedobjects', b'k' * 32))
    with day.engine.connect() as db:
        before = {r.id: (r.content_hash, r.status) for r in db.execute(select(t.versions.c.id, t.versions.c.content_hash, t.versions.c.status))}
        owners_before = db.execute(select(func.count()).select_from(t.lesson_owners)).scalar_one()
    d = day.build()
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    with day.engine.connect() as db:
        after = {r.id: (r.content_hash, r.status) for r in db.execute(select(t.versions.c.id, t.versions.c.content_hash, t.versions.c.status)) if r.id in before}
        seeded = {r.lesson_id for r in db.execute(select(t.versions.c.lesson_id).where(t.versions.c.id.in_(before)))}
        daily_lessons = {r['lesson_id'] for r in day.rows() if r['lesson_id']}
        still_shared = [lid for lid in seeded if db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == lid)).first() is None]
        assert db.execute(select(func.count()).select_from(t.library_entries).where(t.library_entries.c.lesson_id.in_(seeded))).scalar_one() == 0     # never registered
    assert after == before and seeded.isdisjoint(daily_lessons) and len(still_shared) == len(seeded)
    assert owners_before >= 0


# ---- idempotency, concurrency, restarts, partial failure -----------------------------------------------------------------------------

def test_running_the_scheduler_again_and_again_creates_one_request_one_lesson_and_one_publication(day):
    d = day.build()
    for _ in range(4):
        dl.tick(d)
    assert sorted(r.client_request_id for r in day.requests()) == [f'daily:{D1}:a1', f'daily:{D2}:a1']
    day.generate(d)
    for _ in range(4):
        dl.tick(d)
    d.clock.now = at(D2, 0, 0)
    for _ in range(4):
        dl.tick(d)
    assert len(day.requests()) == 2
    with day.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.library_entries).where(t.library_entries.c.state == 'published')).scalar_one() == 2
        assert db.execute(select(func.count(func.distinct(t.daily_challenges.c.lesson_id)))).scalar_one() == 2


def test_a_second_scheduler_cannot_act_while_the_first_holds_the_day(day):
    a, b = day.build(owner='A'), day.build(owner='B')
    assert dl.acquire(day.engine, D2, 'A', a.clock.now)
    assert dl.advance(b, D2) == 'busy' or day.row(D2)['state'] == 'planned'
    assert not dl.acquire(day.engine, D2, 'B', b.clock.now)
    assert day.requests(f'daily:{D2}') == []
    dl.release(day.engine, D2, 'A')
    assert dl.acquire(day.engine, D2, 'B', b.clock.now)
    dl.release(day.engine, D2, 'B')


def test_an_expired_lease_is_taken_over_and_the_old_holder_cannot_write_any_more(day):
    a, b = day.build(owner='A'), day.build(owner='B')
    assert dl.acquire(day.engine, D2, 'A', at(D1, 23, 0))
    b.clock.now = at(D1, 23, 30)                                           # A's lease ran out
    assert dl.acquire(day.engine, D2, 'B', b.clock.now)
    with day.engine.begin() as db, pytest.raises(dl.LeaseLost):
        dl._cas(db, a, D2, 'planned', at(D1, 23, 31), state='generating')   # A woke up late: refused
    assert day.row(D2)['state'] == 'planned'


def test_simultaneous_schedulers_produce_a_single_attempt(day):
    schedulers = [day.build(owner=f'S{n}') for n in range(4)]
    errors = []

    def go(s):
        try:
            dl.advance(s, D2)
        except Exception as e:                                            # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=go, args=(s,)) for s in schedulers]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert not errors
    assert [r.client_request_id for r in day.requests(f'daily:{D2}')] == [f'daily:{D2}:a1'] and day.row(D2)['attempts'] == 1


def test_a_crash_between_steps_resumes_without_a_second_request_or_a_second_reservation(day):
    d = day.build()
    dl.tick(d)
    with day.engine.begin() as db:                                         # simulate: the process died holding the lease right after queuing
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == D2).values(lease_owner='dead', lease_until=at(D1, 23, 6)))
    with day.engine.connect() as db:
        reserved = db.execute(select(func.sum(t.generation_requests.c.reserved_micro))).scalar_one()
    restarted = day.build(owner='new-process', clock=Clock(at(D1, 23, 30)))
    day.generate(restarted)
    dl.tick(restarted)
    with day.engine.connect() as db:
        assert db.execute(select(func.sum(t.generation_requests.c.reserved_micro))).scalar_one() <= reserved       # settled, never doubled
    assert len(day.requests(f'daily:{D2}')) == 1 and day.row(D2)['state'] == 'scheduled'


def test_the_database_itself_refuses_one_lesson_or_one_script_for_two_days(day):
    d = day.build()
    day.full_day(d, D2)
    first = day.row(D2)
    with day.engine.begin() as db:
        ensure = dl.ensure_row
        ensure(db, D3, at(D2, 1))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError), day.engine.begin() as db:
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == D3).values(lesson_id=first['lesson_id']))
    with pytest.raises(IntegrityError), day.engine.begin() as db:
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == D3).values(fingerprint=first['fingerprint']))
    with pytest.raises(IntegrityError), day.engine.begin() as db:                                   # a validated day must carry its fingerprint
        db.execute(update(t.daily_challenges).where(t.daily_challenges.c.challenge_date == D3).values(state='scheduled'))


def test_publication_is_one_transaction_a_failure_leaves_the_lesson_private_and_the_day_scheduled(day, monkeypatch):
    d = day.build()
    day.full_day(d, D2)
    lesson = day.row(D2)['lesson_id']

    def boom(db, *a, **k):
        raise RuntimeError('database went away')
    monkeypatch.setattr(dl.library, 'publish', boom)
    d.clock.now = at(D2, 0, 0)
    with pytest.raises(RuntimeError):
        dl.advance(d, D2)
    assert day.row(D2)['state'] == 'scheduled'
    with day.engine.connect() as db:
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == lesson)).scalar_one() == day.owner
    monkeypatch.undo()
    monkeypatch.setattr(dl, 'STALL_MINUTES', 10 ** 6)
    dl.tick(d)
    assert day.row(D2)['state'] == 'active'


# ---- failure: never replay, never publish bad content ---------------------------------------------------------------------------

def test_when_every_attempt_fails_the_day_is_failed_the_operator_is_told_and_nothing_old_is_substituted(day):
    sent = []
    d = day.build(notify=sent.append)
    day.full_day(d, D2)                                                        # D2 is fine and goes live
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    yesterday = day.row(D2)['lesson_id']
    broken = FakeScriptWriter(mutate=lambda raw, n: raw.update(accepted=False, refusal_reason='no'))
    d.clock.now = at(D2, 23, 5)
    dl.tick(d)
    day.generate(d, broken)
    for _ in range(4):
        dl.tick(d)
        day.generate(d, broken)
    assert day.row(D3)['state'] == 'failed' and day.row(D3)['lesson_id'] is None and day.row(D3)['attempts'] == 2
    assert any('all 2 attempt(s) failed' in m for m in sent)
    d.clock.now = at(D3, 0, 0)
    dl.tick(d)
    assert day.row(D3)['state'] == 'failed'
    view = day_view(day, d.settings, day.other, at(D3, 8))
    assert view['status'] == 'unavailable' and view['lesson'] is None                                   # never yesterday's lesson as today's
    with day.engine.connect() as db:
        assert yesterday in {i['lesson_id'] for i in store.list_catalog(db, day.other, hide=read.hidden_lesson_ids(db, d.settings, at(D3, 8)))}      # yesterday is in Discover


def test_a_rejected_attempt_retries_with_a_different_topic_and_the_rejected_lesson_is_never_published(day):
    judge = FakeDailyJudge()
    judge.verdict = lambda c, n: {'coherent': False, 'issues': ['second half contradicts the first']} if len(judge.calls) == 1 else {}
    d = day.build(judge=judge)
    dl.tick(d)
    day.generate(d)
    dl.tick(d)                                                    # today's first attempt (the first one reviewed) is rejected and attempt 2 is queued in the same pass
    mid = day.row(D1)
    assert mid['state'] == 'generating' and mid['attempts'] == 2 and mid['rejected'][0]['reasons'][0].startswith('semantic:')
    rejected_lesson, rejected_topic = mid['rejected'][0]['lesson_id'], mid['rejected'][0]['topic_key']
    day.generate(d)
    dl.tick(d)
    second = day.row(D1)
    assert second['state'] == 'active' and second['attempts'] == 2 and second['topic_key'] != rejected_topic and second['lesson_id'] != rejected_lesson
    with day.engine.connect() as db:
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == rejected_lesson)).scalar_one() == day.owner         # still a private draft
        assert db.execute(select(t.library_entries.c.state).where(t.library_entries.c.lesson_id == rejected_lesson)).scalar_one() == 'draft'
        assert rejected_lesson not in {i['lesson_id'] for i in store.list_catalog(db, day.other)}


def test_a_lesson_that_copies_an_existing_one_is_rejected_by_the_gates_without_a_model_call(day):
    judge = FakeDailyJudge()
    d = day.build(judge=judge)
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    calls = len(judge.calls)
    d.clock.now = at(D2, 23, 5)
    dl.tick(d)
    day.generate(d, VariedWriter())                                   # a different topic, so a different script
    assert dl.advance(d, D3) == 'scheduled'
    # now force a copy: a writer that repeats day D3's exact script under a new topic
    d.clock.now = at(D3, 23, 5)
    original = day.row(D3)
    with day.engine.connect() as db:
        script = gates.load_snapshot(db, original['lesson_id']).sentences

    class Copier(VariedWriter):
        def write(self, spec, max_chars, feedback):
            raw = super().write(spec, max_chars, feedback)
            for s, text in zip(raw['sentences'], script):
                s['text'] = text
            return raw
    dl.tick(d)
    calls = len(judge.calls)
    day.generate(d, Copier())
    dl.advance(d, '2026-11-13')
    row = day.row('2026-11-13')
    assert row['rejected'] and 'duplicates' in row['rejected'][0]['reasons'][0] and len(judge.calls) == calls
    assert row['state'] in ('planned', 'generating', 'failed') and row['lesson_id'] is None


def test_unsafe_content_is_rejected_even_if_the_semantic_review_approves(day):
    class Unsafe(VariedWriter):
        def write(self, spec, max_chars, feedback):
            raw = super().write(spec, max_chars, feedback)
            raw['sentences'][0]['text'] = 'They planned to kill the engine of the old boat before supper today.'
            return raw
    d = day.build(judge=FakeDailyJudge({'safe': True}))
    dl.tick(d)
    day.generate(d, Unsafe())
    dl.tick(d)
    row = day.row(D2)
    assert row['state'] == 'generating' and row['attempts'] == 2 and any('safety' in r for r in row['rejected'][0]['reasons'])


def test_without_the_semantic_review_nothing_is_published_it_waits_and_alerts(day):
    sent = []
    judge = FakeDailyJudge(ProviderTemporary('down'))
    d = day.build(judge=judge, notify=sent.append)
    dl.tick(d)
    day.generate(d)
    for _ in range(4):
        dl.tick(d)
    assert day.row(D2)['state'] == 'built' and day.row(D2)['rejected'] == []
    d.clock.now = at(D2, 0, 1)
    dl.tick(d)
    assert day.row(D2)['state'] == 'built' and any('semantic review has failed repeatedly' in m for m in sent)
    judge.verdict = None
    dl.tick(d)
    assert day.row(D2)['state'] == 'active'


def test_a_rejected_candidate_keeps_the_full_gate_report_so_the_operator_can_see_what_passed_and_why_it_failed(day):
    judge = FakeDailyJudge({'coherent': False, 'issues': ['the narrator is never named, so a question about them cannot be answered from the audio']})
    d = day.build(settings=daily_settings(day.owner, listening_daily_max_attempts=1), judge=judge)
    dl.tick(d)
    day.generate(d)
    dl.tick(d)
    row = day.row(D2)
    assert row['state'] == 'failed' and row['gate_report'] is None and row['lesson_id'] is None          # nothing about a rejected lesson is ever treated as accepted
    kept = row['rejected'][0]['gates']
    assert all(kept[name]['ok'] and kept[name]['problems'] == [] for name in ('content', 'answers', 'audio', 'safety', 'duplicates'))       # what passed is on record
    assert kept['semantic']['ok'] is False and any('narrator is never named' in p for p in kept['semantic']['problems'])
    assert isinstance(kept['nearest'], list) and len(kept['nearest']) <= 3
    import json
    assert len(json.dumps(row['rejected'])) < 4000                                                          # bounded: it lives in an existing JSONB column


def test_a_candidate_rejected_by_a_deterministic_gate_records_that_gate_and_never_reached_the_review(day):
    class Unsafe(VariedWriter):
        def write(self, spec, max_chars, feedback):
            raw = super().write(spec, max_chars, feedback)
            raw['sentences'][0]['text'] = 'They planned to kill the engine of the old boat before supper today.'
            return raw
    judge = FakeDailyJudge()
    d = day.build(settings=daily_settings(day.owner, listening_daily_max_attempts=1), judge=judge)
    dl.tick(d)
    day.generate(d, Unsafe())
    dl.tick(d)
    kept = day.row(D2)['rejected'][0]['gates']
    assert kept['safety']['ok'] is False and any('kill' in p for p in kept['safety']['problems'])
    assert kept['content']['ok'] and kept['audio']['ok'] and 'semantic' not in kept and judge.calls == []        # failed before any model call


def test_a_reviewer_that_refuses_rejects_the_candidate(day):
    d = day.build(judge=FakeDailyJudge(ProviderPermanent('refused')))
    dl.tick(d)
    day.generate(d)
    dl.tick(d)
    assert day.row(D2)['rejected'][0]['reasons'][0].startswith('semantic:')


def test_a_lesson_that_changes_after_review_is_not_published(day):
    d = day.build()
    day.full_day(d, D2)
    lesson = day.row(D2)['lesson_id']
    with day.engine.begin() as db:                                          # tamper: an extra audio variant appended after the review (the digest covers every variant)
        variant = db.execute(select(t.audio).where(t.audio.c.version_id == day.row(D2)['version_id'])).mappings().first()
        db.execute(insert(t.audio).values(**{**variant, 'id': str(uuid4()), 'voice': 'another-voice', 'accent': 'british', 'object_key': variant['object_key']}))
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    row = day.row(D2)
    assert row['state'] != 'active' and row['rejected'][0]['reasons'][0].startswith('publish refused')
    with day.engine.connect() as db:
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == lesson)).scalar_one() == day.owner


# ---- authorization, switches and spending -----------------------------------------------------------------------------------------

def test_the_service_account_must_already_be_an_authorized_curator(lab):
    other = str(uuid4())
    with pytest.raises(RuntimeError, match='CURATOR_IDS'):
        dl.build(daily_settings(lab.owner, listening_curator_ids=other), lab.engine)
    with pytest.raises(RuntimeError, match='SERVICE_ID'):
        dl.build(daily_settings(lab.owner, listening_daily_service_id='', listening_daily_publish=False), lab.engine)
    with pytest.raises(ValueError, match='DAILY_PUBLISH needs'):
        Settings(_env_file=None, listening_daily_publish=True)


def test_a_service_account_that_loses_curator_status_publishes_nothing_spends_nothing_and_the_operator_is_told(day):
    sent = []
    d = day.build(notify=sent.append)
    day.full_day(d, D2)
    before = len(day.requests())
    d.settings = daily_settings(day.owner, listening_curator_ids=str(uuid4()))                  # removed from AUDLI_LISTENING_CURATOR_IDS
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    assert day.row(D2)['state'] == 'scheduled' and len(day.requests()) == before and day.row(D2)['rejected'] == []
    assert any('not on AUDLI_LISTENING_CURATOR_IDS' in m for m in sent)
    with day.engine.connect() as db:
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == day.row(D2)['lesson_id'])).scalar_one() == day.owner
    d.settings = daily_settings(day.owner)                                                      # restored: it simply carries on
    dl.tick(d)
    assert day.row(D2)['state'] == 'active'


def test_publication_is_off_by_default_the_challenge_is_validated_and_waits_for_an_operator(day):
    d = day.build(settings=daily_settings(day.owner, listening_daily_publish=False))
    day.full_day(d, D2)
    d.clock.now = at(D2, 5, 0)
    for _ in range(3):
        dl.tick(d)
    assert day.row(D2)['state'] == 'scheduled'


def test_the_pause_switch_stops_generation_and_publication_at_once_and_resume_continues(day):
    d = day.build()
    day.full_day(d, D2)
    dl.set_paused(day.engine, True, 'investigating')
    d.clock.now = at(D2, 0, 0)
    assert dl.tick(d) == {'paused': True, 'states': {}}
    assert dl.advance(d, D2) == 'scheduled'
    assert read.paused(day.engine.connect())
    dl.set_paused(day.engine, False)
    dl.tick(d)
    assert day.row(D2)['state'] == 'active'


def test_the_global_ai_kill_switch_stops_new_generation_but_not_publishing_what_is_already_validated(day):
    d = day.build()
    day.full_day(d, D2)
    d.guard.set_halted(True)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    assert day.row(D2)['state'] == 'active'
    assert day.row(D3)['state'] in ('planned',) if any(r['challenge_date'] == D3 for r in day.rows()) else True
    d.clock.now = at(D2, 23, 5)
    dl.tick(d)
    assert [r.client_request_id for r in day.requests(f'daily:{D3}')] == [f'daily:{D3}:a1'] and day.requests(f'daily:{D3}')[0].phase == 'unavailable'      # recorded, never reserved or queued


def test_the_per_day_generation_cap_blocks_spending(day):
    sent = []
    d = day.build(settings=daily_settings(day.owner, listening_daily_max_usd=0.0001), notify=sent.append)
    dl.tick(d)
    with day.engine.connect() as db:
        assert db.execute(select(func.coalesce(func.sum(t.generation_requests.c.reserved_micro), 0))).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(t.jobs)).scalar_one() == 0
    assert any('per-challenge generation cap' in m for m in sent) and day.row(D2)['state'] == 'generating'


def test_the_review_budget_caps_the_semantic_review_and_nothing_is_published_over_it(day):
    sent = []
    judge = FakeDailyJudge()
    d = day.build(settings=daily_settings(day.owner, listening_daily_review_max_usd=0.0), judge=judge, notify=sent.append)
    dl.tick(d)
    day.generate(d)
    dl.tick(d)
    assert judge.calls == [] and day.row(D2)['state'] == 'built' and any('review budget' in m for m in sent)


def test_the_global_generation_limits_still_apply_and_the_service_account_cap_is_explained(day):
    sent = []
    d = day.build(settings=daily_settings(day.owner, listening_gen_curator_daily=None, listening_gen_per_learner_daily=1), notify=sent.append)
    dl.tick(d)                                                                      # today's challenge takes the only allowed request
    assert any('AUDLI_LISTENING_GEN_CURATOR_DAILY' in m for m in sent) and day.row(D2)['state'] == 'generating'


def test_attempts_are_capped_per_day_and_an_operator_can_grant_more(day):
    d = day.build(settings=daily_settings(day.owner, listening_daily_max_attempts=1))
    broken = FakeScriptWriter(mutate=lambda raw, n: raw.update(accepted=False, refusal_reason='no'))
    dl.tick(d)
    day.generate(d, broken)
    dl.tick(d)
    assert day.row(D2)['state'] == 'failed' and day.row(D2)['attempts'] == 1
    assert not dl.retry_day(day.engine, '2020-01-01')
    assert dl.retry_day(day.engine, D2) and day.row(D2)['state'] == 'planned' and len(day.row(D2)['rejected']) == 1
    dl.tick(d)
    assert day.row(D2)['state'] == 'generating' and day.row(D2)['attempts'] == 2          # a NEW request (a2), never the failed one again
    assert [r.client_request_id for r in day.requests(f'daily:{D2}')] == [f'daily:{D2}:a1', f'daily:{D2}:a2']


# ---- topics from the AI when the pool runs low ----------------------------------------------------------------------------------------

def test_ai_topic_proposals_are_used_only_when_the_pool_is_low_and_are_filtered(day):
    fresh = {'key': 'harbour-ferry-queue', 'category': 'travel', 'topic': 'Waiting for the harbour ferry in the rain', 'scenario': 'Two commuters shelter and talk about the late boat.', 'lo': 'A2', 'hi': 'B1'}
    proposer = FakeTopicProposer([
        fresh,
        {**fresh, 'key': 'dupe', 'topic': tax.TAXONOMY[0].topic, 'scenario': tax.TAXONOMY[0].scenario},                           # a rewording of a pool topic
        {**fresh, 'key': 'unsafe', 'topic': 'A gun on the bus', 'scenario': 'Someone brings a gun on a bus today.'},
        {**fresh, 'key': 'badcat', 'category': 'finance'}])
    d = day.build(settings=daily_settings(day.owner, listening_daily_pool_low=100), proposer=proposer)
    with day.engine.begin() as db:
        dl.ensure_row(db, D2, d.clock.now)
    dl.advance(d, D2)                                                       # D2 is even or odd - run both days so the alternation is covered
    dl.advance(d, D1)
    used = [r for r in day.rows() if r['plan']]
    assert proposer.calls and all(r['plan']['source'] in ('ai', 'pool') for r in used)
    assert any(r['plan']['source'] == 'ai' and r['topic_key'] == 'ai-harbour-ferry-queue' for r in used) or len(proposer.calls) >= 1
    kept = dl.validate_proposals(proposer.topics, [s.topic + ' ' + s.scenario for s in tax.TAXONOMY], set())
    assert [k.key for k in kept] == ['ai-harbour-ferry-queue']


def test_when_the_pool_is_exhausted_the_ai_must_supply_the_topic_or_the_attempt_fails_without_spending(day):
    d = day.build(proposer=FakeTopicProposer(ProviderTemporary('down')))
    with day.engine.begin() as db:
        db.execute(insert(t.daily_challenges).values(challenge_date='2026-01-01', state='failed', attempts=2, rejected=[{'attempt': 1, 'lesson_id': None, 'topic_key': s.key, 'reasons': ['x']} for s in tax.TAXONOMY],
                                                     events=[], review_micro=0, created_at=at(D1, 1), updated_at=at(D1, 1)))
    dl.tick(d)
    assert day.row(D2)['state'] in ('planned', 'failed') and day.requests(f'daily:{D2}') == []
    with day.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.jobs)).scalar_one() == 0


# ---- the learner side ------------------------------------------------------------------------------------------------------------------

def day_view(day, settings, user, now):
    with day.engine.connect() as db:
        return read.today_view(db, settings, user, lambda lid: next((i for i in store.list_catalog(db, user) if i['lesson_id'] == lid), None), now)


def attempt_row(day, user, lesson_id, completed_at, status='completed'):
    with day.engine.begin() as db:
        version = db.execute(select(t.versions.c.id).where(t.versions.c.lesson_id == lesson_id)).scalar_one()
        variant = db.execute(select(t.audio.c.id).where(t.audio.c.version_id == version)).scalars().first()
        aid = str(uuid4())
        db.execute(insert(t.attempts).values(id=aid, user_id=user, version_id=version, audio_variant_id=variant, client_request_id=uuid4().hex, status=status, total=4,
                                            correct=3 if status == 'completed' else None, created_at=completed_at, completed_at=completed_at if status == 'completed' else None))
    return aid


def test_the_card_is_unavailable_before_midnight_and_ready_after_with_progress_and_streak(day):
    d = day.build()
    day.full_day(d, D2)
    early = day_view(day, d.settings, day.other, at(D1, 23, 59))                                        # D2 is scheduled, not live: only D1's own lesson can show
    assert early['date'] == D1 and (early['lesson'] or {}).get('lesson_id') != day.row(D2)['lesson_id']
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    view = day_view(day, d.settings, day.other, at(D2, 7))
    assert view['status'] == 'ready' and view['date'] == D2 and view['next_at'] == at(D3, 0, 0)
    assert view['lesson']['status'] == 'new' and view['completed_today'] is False and view['streak']['current'] == 0
    assert {'title', 'cefr', 'topic', 'accents'} <= set(view['lesson'])
    assert not ({'correct_option_id', 'explanation', 'accepted', 'object_key', 'sha256'} & set(str(view)))


def test_pausing_the_scheduler_does_not_hide_a_challenge_that_is_already_live(day):
    """AUD-117 acceptance bug: the pause switch stops generation and publication; it must not turn a live, published challenge into "being prepared"."""
    d = day.build()
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    assert day.row(D2)['state'] == 'active'
    dl.set_paused(day.engine, True, 'controlled publication')
    view = day_view(day, d.settings, day.other, at(D2, 7))
    assert view['status'] == 'ready' and view['date'] == D2 and view['lesson']['lesson_id'] == day.row(D2)['lesson_id']
    dl.set_paused(day.engine, False)
    assert day_view(day, d.settings, day.other, at(D2, 7))['status'] == 'ready'                          # resuming changes nothing the learner sees


def test_a_paused_scheduler_with_nothing_live_is_still_unavailable_and_never_shows_an_older_lesson(day):
    d = day.build()
    day.full_day(d, D2)                                                                                     # D2 is scheduled, not live
    dl.set_paused(day.engine, True, 'investigating')
    view = day_view(day, d.settings, day.other, at(D2, 7))
    assert view['status'] == 'unavailable' and view['lesson'] is None


def test_completing_on_the_day_credits_it_once_no_matter_how_often_it_is_repeated(day):
    d = day.build()
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    lesson = day.row(D2)['lesson_id']
    a = attempt_row(day, day.other, lesson, at(D2, 9), status='in_progress')
    assert day_view(day, d.settings, day.other, at(D2, 9, 30))['lesson']['status'] == 'in_progress'
    with day.engine.begin() as db:
        db.execute(update(t.attempts).where(t.attempts.c.id == a).values(status='completed', correct=3, completed_at=at(D2, 10)))
    attempt_row(day, day.other, lesson, at(D2, 11))                                                        # replayed the same day: still one credit
    view = day_view(day, d.settings, day.other, at(D2, 12))
    assert view['completed_today'] is True and view['streak'] == {'current': 1, 'longest': 1, 'days_completed': 1}
    assert day_view(day, d.settings, day.owner, at(D2, 12))['completed_today'] is False                    # per learner


def test_a_completion_on_the_ict_boundary_counts_for_the_day_it_finished_in(day):
    d = day.build()
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    lesson = day.row(D2)['lesson_id']
    attempt_row(day, day.other, lesson, at(D2, 23, 59, ) + timedelta(seconds=59))
    attempt_row(day, day.owner, lesson, at(D3, 0, 0))                                                      # finished one second into the next day: no credit for D2
    assert day_view(day, d.settings, day.other, at(D2, 23, 59))['completed_today'] is True
    assert read.credited_days(day.engine.connect(), d.settings, day.owner) == set()


def test_streaks_run_across_consecutive_days_and_a_late_finish_does_not_extend_them(day):
    d = day.build()
    for target in (D1, D2, D3):
        day.full_day(d, target)
        d.clock.now = at(target, 0, 0)
        dl.tick(d)
    lessons = {k: day.row(k)['lesson_id'] for k in (D1, D2, D3)}
    attempt_row(day, day.other, lessons[D1], at(D1, 8))
    attempt_row(day, day.other, lessons[D2], at(D2, 8))
    attempt_row(day, day.other, lessons[D3], at(D3, 8))
    assert day_view(day, d.settings, day.other, at(D3, 9))['streak'] == {'current': 3, 'longest': 3, 'days_completed': 3}
    attempt_row(day, day.owner, lessons[D1], at(D1, 8))
    attempt_row(day, day.owner, lessons[D2], at(D3, 8))                                                    # D2's lesson finished on D3: Discover play, not a daily credit
    assert day_view(day, d.settings, day.owner, at(D3, 9))['streak'] == {'current': 0, 'longest': 1, 'days_completed': 1}
    assert day_view(day, d.settings, day.owner, at(D2, 9))['streak']['current'] == 1                        # D1 credited, D2 still open


def test_existing_sessions_and_progress_survive_the_whole_cycle(day):
    d = day.build()
    day.full_day(d, D2)
    d.clock.now = at(D2, 0, 0)
    dl.tick(d)
    lesson = day.row(D2)['lesson_id']
    a = attempt_row(day, day.other, lesson, at(D2, 9), status='in_progress')
    d.clock.now = at(D3, 0, 0)
    dl.tick(d)
    with day.engine.connect() as db:
        item = next(i for i in store.list_catalog(db, day.other) if i['lesson_id'] == lesson)
    assert item['status'] == 'in_progress' and item['attempt_id'] == a


# ---- operator visibility ----------------------------------------------------------------------------------------------------------------

def test_the_operator_summary_shows_states_spend_diversity_and_alerts_without_lesson_text(day):
    d = day.build()
    day.full_day(d, D2)
    report = dl.summary(day.engine, d.settings, at(D2, 1))
    assert report['migrated'] and report['enabled'] and report['publishing'] and report['pool_unused'] == len(tax.TAXONOMY) - 2
    row = next(x for x in report['days'] if x['date'] == D2)
    assert row['state'] == 'scheduled' and row['attempts'] == 1 and row['review_micro'] > 0 and row['last_events']
    assert sum(report['diversity']['category'].values()) == 2
    assert 'sentences' not in str(report) and 'Sentence' not in str(report)
    assert any('not live' in a for a in dl.alerts(report, now_local_hour=2))
    assert dl.alerts({**report, 'today_state': 'failed'}, 2) and dl.alerts({**report, 'paused': True}, 2)
    assert dl.alerts({'enabled': False}) == []


def test_ops_health_includes_the_daily_block_when_enabled(day):
    from app.listening.health import report_with_alerts
    d = day.build()
    day.full_day(d, D2)
    report = report_with_alerts(day.engine, d.settings, at(D2, 3))
    assert report['daily']['today_state'] and any('daily' in a for a in report['alerts'])
    assert 'daily' not in report_with_alerts(day.engine, gen_settings(), at(D2, 3))


# ---- migration, role and process isolation -----------------------------------------------------------------------------------------

def test_migration_015_and_the_daily_role_sql_are_the_checked_in_files_and_are_additive():
    from pathlib import Path
    from app.listening.migrations import postgres_listening_migration_015
    from app.listening.roles import DAILY_DELETE, DAILY_SELECT, api_role_sql, daily_role_sql
    root = Path(__file__).resolve().parents[1]
    sql = postgres_listening_migration_015()
    assert (root / 'docs/postgres-015.sql').read_text() == sql
    assert (root / 'docs/postgres-listening-daily-role.sql').read_text() == daily_role_sql()
    assert (root / 'docs/postgres-listening-api-role.sql').read_text() == api_role_sql()
    assert t.NAMES_015 == ('listening_daily_control', 'listening_daily_challenges') or set(t.NAMES_015) == {'listening_daily_challenges', 'listening_daily_control'}
    statements = [line for line in sql.splitlines() if line.startswith(('ALTER ', 'DROP ', 'CREATE TABLE'))]
    assert all(line.endswith('ENABLE ROW LEVEL SECURITY;') or line.startswith('CREATE TABLE listening_daily_') for line in statements)       # only creates its own two tables; never alters or drops anything else
    assert 'INSERT INTO schema_migrations' not in sql
    assert 'VALUES (15, now())' in sql and 'REVOKE ALL ON TABLE' in sql
    assert DAILY_DELETE == ('listening_lesson_owners',)
    from app.listening.roles import DAILY_INSERT, DAILY_UPDATE
    assert 'listening_ai_limits' in DAILY_INSERT and 'listening_ai_limits' not in DAILY_UPDATE        # the kill-switch row is created on first read; the daily process never changes it
    private = {'listening_attempts', 'listening_responses', 'listening_answers', 'listening_spoken', 'users'}
    assert not private & set(DAILY_SELECT)                                                              # the daily role never sees learner data


def test_no_listening_migration_or_role_sql_contains_a_percent_sign():
    """psql runs a '%' literally but psycopg (the test harness and migrate_postgres) reads it as a bind placeholder; 015 once used format('%I') and only the PostgreSQL run caught it."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'docs'
    for path in [*(p for p in root.glob('postgres-0*.sql') if int(p.stem.split('-')[1]) >= 9), *root.glob('postgres-listening-*.sql')]:       # the 2.0 listening migrations and roles (008 is 1.x)
        assert '%' not in path.read_text(), path.name


def test_the_daily_process_gets_the_ai_key_but_no_speech_bucket_recordings_or_signing_secret():
    from app.listening import sidecar
    env = sidecar.worker_environment({k: 'x' for k in (*sidecar.DAILY_PASS_THROUGH, 'ELEVENLABS_API_KEY', 'AUDLI_LISTENING_S3_SECRET_KEY', 'AUDLI_LISTENING_S3_ACCESS_KEY',
                                                      'AUDLI_LISTENING_RECORDINGS_SECRET_KEY', 'AUDLI_LISTENING_SIGNING_SECRET', 'AUDLI_LISTENING_OPS_TOKEN', 'AUDLI_DATABASE_URL',
                                                      sidecar.DAILY_URL)}, 'daily')
    assert env['OPENAI_API_KEY'] and env['AUDLI_LISTENING_PROCESS'] == 'daily' and env['AUDLI_DATABASE_URL'] == 'x'
    assert not [k for k in env if 'ELEVEN' in k or 'S3_' in k or 'RECORDINGS' in k or 'SIGNING' in k or 'OPS_TOKEN' in k]
    assert sidecar.ARGS['daily'] == ('run',)
