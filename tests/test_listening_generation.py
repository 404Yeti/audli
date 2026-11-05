"""Audli 2.0 Milestone 05: AI lesson generation. Fake providers throughout (no paid call); real jobs, ledger, validation, publishing and ownership.
Runs on SQLite always and on real PostgreSQL when AUDLI_TEST_POSTGRES_URL is set."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.listening import generation as g, generation_jobs as gj, jobs, schema as t, store
from app.listening.api import prepare
from app.listening.generation import FakeInterpreter, FakeScriptWriter, GenerationRefused, Ledger, Spec
from app.listening.objects import LocalObjectStore
from app.listening.speech import FakeSpeech, SpeechTemporary
from app.storage.repository import utcnow
from test_listening import make_repository  # noqa: F401  (fixture)

SPEC = Spec(topic='ordering coffee in New York', cefr='B1', accent='american', minutes=2, format='dialogue', scenario='a busy morning')
VOICES = {'american': ('v-a1', 'v-a2'), 'british': ('v-b1', 'v-b2'), 'australian': ('v-c1', 'v-c2')}


def settings(**over):
    return Settings(_env_file=None, listening_enabled=True, listening_generation_enabled=True, **over)


@pytest.fixture
def lab(make_repository, tmp_path):
    repo = make_repository(None)                                          # accounts mode: learners exist once a scoped repository has been opened for them
    owner, other = str(uuid4()), str(uuid4())
    for learner in (owner, other):
        repo.for_learner(learner)
    prepare(repo.engine, generation=True)

    def deps(**over):
        s = over.pop('settings', settings())
        speech = over.pop('speech', FakeSpeech())
        engine = over.pop('engine', repo.engine)
        return g.Deps(engine=engine, settings=s, ledger=Ledger(engine, s), objects=LocalObjectStore(tmp_path / 'lessons', b'k' * 32),
                      speech=gj.MeteredSpeech(speech, g.CREDIT_MULTIPLIER[s.listening_gen_speech_model]), writer=over.pop('writer', FakeScriptWriter()),
                      interpreter=over.pop('interpreter', FakeInterpreter()), voices=VOICES, halted=over.pop('halted', None))
    return SimpleNamespace(repo=repo, engine=repo.engine, owner=owner, other=other, deps=deps, tmp=tmp_path)


def start(lab, d, user=None, spec=SPEC, request='r1'):
    user = user or lab.owner
    with lab.engine.begin() as db:
        request_id, _ = gj.create_generation(db, user, request, spec, [], None)
    gj.finalize_reservation(lab.engine, d.ledger, d.settings, user, request_id, spec)
    return request_id


def run(lab, d, owner='w1'):
    with d.engine.begin() as db:
        job = jobs.claim(db, owner)
    assert job is not None
    return gj.process(d, job)


def view(lab, user, request_id):
    with lab.engine.connect() as db:
        return gj.request_view(db, user, request_id)


def limits(lab):
    with lab.engine.connect() as db:
        return {r.scope: (r.reserved_micro, r.reserved_credits, r.lessons) for r in db.execute(select(t.generation_limits))}


# ---- interpreting a request ----------------------------------------------------------------------------------------------------

def interpret(lab, d, text, request='i1'):
    with lab.engine.begin() as db:
        request_id, _ = gj.create_interpretation(db, lab.owner, request, text)
    assert run(lab, d) == 'done'
    return view(lab, lab.owner, request_id)


def test_a_natural_language_request_becomes_reviewable_settings_with_honest_notes(lab):
    d = lab.deps()
    result = interpret(lab, d, 'Create a B1 listening lesson about ordering coffee in New York, with American accents, around three minutes long.')
    assert result['phase'] == 'interpreted' and result['spec']['topic'] == 'ordering coffee in new york'
    assert result['spec'] == {**result['spec'], 'cefr': 'B1', 'accent': 'american', 'minutes': 2, 'format': 'dialogue'}
    assert 'minutes_limited:2' in result['notes'] and 'defaulted:format' in result['notes']          # three minutes asked, two supported: said so


def test_an_unavailable_accent_is_replaced_and_said_so_and_a_missing_topic_asks_one_question(lab):
    d = lab.deps(interpreter=FakeInterpreter({'topic': 'a train booking', 'accent': 'Irish', 'cefr': 'A2'}))
    result = interpret(lab, d, 'a train booking in Irish please')
    assert result['spec']['accent'] == 'american' and 'accent_unavailable' in result['notes']
    d = lab.deps(interpreter=FakeInterpreter({'question': 'What would you like it to be about?'}))
    asked = interpret(lab, d, 'make me a lesson', request='i2')
    assert asked['phase'] == 'needs_input' and asked['question'] and asked['spec'] is None


def test_harmful_requests_are_rejected_without_a_lesson_or_spending(lab):
    d = lab.deps(interpreter=FakeInterpreter({'rejected_reason': 'not allowed'}))
    with lab.engine.begin() as db:
        request_id, _ = gj.create_interpretation(db, lab.owner, 'bad', 'something harmful')
    assert run(lab, d) == 'failed'
    seen = view(lab, lab.owner, request_id)
    assert seen['phase'] == 'rejected' and 'can’t make a lesson' in seen['failure'] and limits(lab) == {}


def test_interpretation_is_idempotent_and_limited_per_day(lab):
    with lab.engine.begin() as db:
        first = gj.create_interpretation(db, lab.owner, 'same', 'about tea')
        assert gj.create_interpretation(db, lab.owner, 'same', 'about tea') == (first[0], False)
        for n in range(gj.INTERPRETATIONS_PER_DAY - 1):
            gj.create_interpretation(db, lab.owner, f'n{n}', 'about tea')
        with pytest.raises(GenerationRefused) as refused:
            gj.create_interpretation(db, lab.owner, 'one-too-many', 'about tea')
    assert refused.value.reason == 'daily_limit'


# ---- a complete generation -----------------------------------------------------------------------------------------------------------

def test_a_lesson_is_ready_only_when_audio_cues_exercises_and_ownership_are_all_in_place(lab):
    d = lab.deps()
    request_id = start(lab, d)
    assert view(lab, lab.owner, request_id)['phase'] == 'queued'
    assert run(lab, d) == 'done'
    done = view(lab, lab.owner, request_id)
    assert done['phase'] == 'ready' and done['lesson_id'] and done['title'] and done['failure'] is None
    with lab.engine.connect() as db:
        detail = store.lesson_detail(db, done['lesson_id'], lab.owner)
        audio = detail['audio'][0]
        assert audio['accent'] == 'american' and audio['duration_ms'] > 10_000 and len(audio['alignment']) == len(detail['sentences'])
        kinds = [r.kind for r in db.execute(select(t.exercises.c.kind).join(t.versions, t.versions.c.id == t.exercises.c.version_id)
                                            .where(t.versions.c.lesson_id == done['lesson_id']).order_by(t.exercises.c.position))]
        assert kinds == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']
        key = db.execute(select(t.audio.c.object_key).where(t.audio.c.id == audio['id'])).scalar_one()
    assert d.objects.exists(key)                                              # the audio is really stored
    assert d.speech.spent > 0 and all(voice in ('v-a1', 'v-a2') for _, voice in d.speech.inner.calls)


def test_generated_lessons_are_private_to_their_owner_in_every_read_and_write(lab):
    d = lab.deps()
    request_id = start(lab, d)
    run(lab, d)
    lesson_id = view(lab, lab.owner, request_id)['lesson_id']
    with lab.engine.connect() as db:
        assert lesson_id in [i['lesson_id'] for i in store.list_catalog(db, lab.owner)]
        assert lesson_id not in [i['lesson_id'] for i in store.list_catalog(db, lab.other)]       # not in anyone else's library
        assert lesson_id not in [i['lesson_id'] for i in store.list_catalog(db, None)]
        with pytest.raises(store.NotFound):
            store.lesson_detail(db, lesson_id, lab.other)
        assert view(lab, lab.other, request_id) is None                                          # nor can they see the request
    with lab.engine.begin() as db:
        with pytest.raises(store.NotFound):
            store.create_attempt(db, lab.other, lesson_id, uuid4().hex, None)
        attempt, created = store.create_attempt(db, lab.owner, lesson_id, uuid4().hex, None)
    assert created and attempt


def test_the_exercises_are_derived_from_the_script_and_the_sequence_is_never_shown_solved(lab):
    d = lab.deps()
    start(lab, d)
    run(lab, d)
    with lab.engine.connect() as db:
        version = db.execute(select(t.versions)).mappings().one()
        rubrics = {r.exercise_id: r.data for r in db.execute(select(t.rubrics))}
        exercises = {r.kind: r for r in db.execute(select(t.exercises))}
    texts = [s['text'] for s in version['sentences']]
    blank = exercises['fill_blank'].prompt
    accepted = rubrics[exercises['fill_blank'].id]['accepted'][0]
    assert blank.count('___') == 1 and any(blank.replace('___', accepted) == text for text in texts)      # the answer is in the script, once
    order = rubrics[exercises['sequencing'].id]['order']
    shown = [i['id'] for i in exercises['sequencing'].options]
    assert sorted(shown) == sorted(order) and shown != order


def test_an_invalid_script_is_corrected_once_and_a_second_failure_publishes_nothing_and_gives_the_reservation_back(lab):
    broken = lambda raw, call: raw['fill'].update(answer='nonexistent phrase')
    writer = FakeScriptWriter(mutate=lambda raw, call: broken(raw, call) if call == 1 else None)
    d = lab.deps(writer=writer)
    request_id = start(lab, d)
    assert run(lab, d) == 'done' and writer.calls == 2 and 'fill.answer' in writer.feedback[1]            # the model was told what was wrong
    assert view(lab, lab.owner, request_id)['phase'] == 'ready'
    always = FakeScriptWriter(mutate=lambda raw, call: raw['fill'].update(answer='nonexistent phrase'))
    d2 = lab.deps(writer=always)
    before = limits(lab)
    second = start(lab, d2, request='r2')
    assert run(lab, d2, 'w2') == 'failed' and always.calls == 2
    seen = view(lab, lab.owner, second)
    assert seen['phase'] == 'failed' and 'quality checks' in seen['failure'] and seen['lesson_id'] is None
    with lab.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.lessons)).scalar_one() == 1               # only the first lesson exists
    day = utcnow().strftime('%Y-%m-%d')
    assert limits(lab)[day][0] == before[day][0] and limits(lab)[day][1] == before[day][1]            # the failed request's reservation was returned
    assert d2.speech.spent == 0                                                                       # and nothing was ever synthesised


@pytest.mark.parametrize('rule', ['too_long', 'too_short', 'one_speaker', 'bad_mcq', 'bad_sequence'])
def test_each_deterministic_check_refuses_a_bad_script(rule):
    raw = FakeScriptWriter().write(SPEC, g.estimate_chars(2), None)
    if rule == 'too_long':
        raw['sentences'] += raw['sentences'] * 3
    elif rule == 'too_short':                                   # the first production lesson: a "2-minute" request that came back as ~40 seconds
        raw['sentences'] = raw['sentences'][:6]
    elif rule == 'one_speaker':
        raw['speakers'] = raw['speakers'][:1]
    elif rule == 'bad_mcq':
        raw['mcq']['correct_option_id'] = 'a'
    else:
        raw['sequence']['events'][1]['sentence_index'] = 0
    with pytest.raises(ValueError):
        g.assemble(SPEC, raw, 'req', g.estimate_chars(2))


def test_a_refusal_by_the_model_is_a_rejection_not_a_failure_to_retry(lab):
    d = lab.deps(writer=FakeScriptWriter(mutate=lambda raw, call: raw.update(accepted=False, refusal_reason='no')))
    request_id = start(lab, d)
    assert run(lab, d) == 'failed'
    assert view(lab, lab.owner, request_id)['phase'] == 'rejected'


# ---- limits --------------------------------------------------------------------------------------------------------------------------

def test_two_lessons_per_learner_per_day_then_the_unavailable_state_without_any_job(lab):
    d = lab.deps()
    start(lab, d, request='a'); start(lab, d, request='b')
    with lab.engine.begin() as db:
        third, _ = gj.create_generation(db, lab.owner, 'c', SPEC, [], None)
    with pytest.raises(GenerationRefused) as refused:
        gj.finalize_reservation(lab.engine, d.ledger, d.settings, lab.owner, third, SPEC)
    assert refused.value.reason == 'daily_limit'
    assert view(lab, lab.owner, third)['phase'] == 'unavailable' and view(lab, lab.owner, third)['unavailable_reason'] == 'daily_limit'
    with lab.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.jobs).where(t.jobs.c.dedupe_key == f'generate:{third}')).scalar_one() == 0
    assert d.ledger.availability(lab.owner) == (False, 'daily_limit')
    assert d.ledger.availability(lab.other)[0] is True                                       # another learner is unaffected


def test_the_daily_dollar_ceiling_and_the_monthly_credit_allowance_are_enforced_atomically(lab):
    tight = settings(listening_gen_daily_budget_usd=0.40)
    d = lab.deps(settings=tight)
    start(lab, d, request='a')
    with pytest.raises(GenerationRefused) as refused:
        start(lab, d, user=lab.other, request='b')
    assert refused.value.reason == 'budget'
    small = settings(listening_gen_monthly_credits=1_000)
    d2 = lab.deps(settings=small)
    with pytest.raises(GenerationRefused) as refused:
        start(lab, d2, user=lab.other, request='c')
    assert refused.value.reason == 'credits'
    month = utcnow().strftime('%Y-%m')
    assert limits(lab)[month][1] <= small.listening_gen_monthly_credits or limits(lab)[month][1] <= g.credit_cost('eleven_flash_v2_5', g.estimate_chars(2))


def test_credits_are_counted_at_the_models_multiplier_and_reconciled_to_actual_usage(lab):
    flash, multilingual = g.credit_cost('eleven_flash_v2_5', 1000), g.credit_cost('eleven_multilingual_v2', 1000)
    assert (flash, multilingual) == (500, 1000)
    d = lab.deps()
    request_id = start(lab, d)
    month = utcnow().strftime('%Y-%m')
    reserved = limits(lab)[month][1]
    assert reserved == g.credit_cost('eleven_flash_v2_5', g.estimate_chars(2))
    run(lab, d)
    with lab.engine.connect() as db:
        row = db.execute(select(t.generation_requests.c.reserved_credits, t.generation_requests.c.actual_credits).where(t.generation_requests.c.id == request_id)).one()
    assert 0 < row.actual_credits <= reserved and row.reserved_credits == row.actual_credits and limits(lab)[month][1] == row.actual_credits < reserved


def test_the_provider_being_nearly_out_of_credits_stops_before_any_synthesis(lab):
    d = lab.deps(speech=FakeSpeech(remaining=150))
    request_id = start(lab, d)
    assert run(lab, d) == 'failed'
    assert view(lab, lab.owner, request_id)['phase'] == 'failed' and d.speech.spent == 0
    month = utcnow().strftime('%Y-%m')
    assert limits(lab)[month][1] == 0                                                          # the reservation was handed back
    unreadable = lab.deps(speech=FakeSpeech(remaining=None))
    second = start(lab, unreadable, request='r2')
    assert run(lab, unreadable, 'w2') == 'failed' and view(lab, lab.owner, second)['failure']


def test_the_kill_switch_pauses_generation_and_the_job_waits_instead_of_failing(lab):
    d = lab.deps(halted=lambda: True)
    request_id = start(lab, d)
    assert run(lab, d) == 'retry'
    assert view(lab, lab.owner, request_id)['phase'] == 'queued' and d.speech.spent == 0


def test_a_temporary_speech_failure_is_retried_and_the_finished_audio_is_never_paid_for_twice(lab):
    flaky = FakeSpeech()
    original = flaky.synthesize
    state = {'fail': True}

    def synth(text, voice):
        if state['fail'] and len(flaky.calls) >= 3:
            raise SpeechTemporary('network')
        return original(text, voice)
    flaky.synthesize = synth
    d = lab.deps(speech=flaky)
    request_id = start(lab, d)
    assert run(lab, d) == 'retry'
    state['fail'] = False
    with lab.engine.begin() as db:                                              # make the retry eligible now
        db.execute(t.jobs.update().values(run_after=utcnow() - timedelta(seconds=1)))
    assert run(lab, d, 'w2') == 'done'
    assert view(lab, lab.owner, request_id)['phase'] == 'ready'


def test_a_redelivered_job_for_a_finished_lesson_does_nothing(lab):
    d = lab.deps()
    request_id = start(lab, d)
    run(lab, d)
    spent = d.speech.spent
    with lab.engine.begin() as db:
        job_id = jobs.enqueue(db, lab.owner, 'generate', 'generate:again', {'request_id': request_id})
    assert run(lab, d, 'w3') == 'done' and d.speech.spent == spent
    with lab.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(t.lessons)).scalar_one() == 1


# ---- the generator's own database role (PostgreSQL only) ------------------------------------------------------------------------------

def role_engine(engine, role):
    from sqlalchemy import create_engine, event
    restricted = create_engine(engine.url, poolclass=__import__('sqlalchemy').pool.NullPool)

    @event.listens_for(restricted, 'connect')
    def switch(dbapi, record):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
        dbapi.commit()
    return restricted


@pytest.fixture
def grole(lab):
    if lab.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    from app.listening.roles import generator_role_sql
    name = 'lg_' + uuid4().hex[:10]
    with lab.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN')
        db.exec_driver_sql(generator_role_sql(name))
    yield lab, name
    with lab.engine.begin() as db:
        db.exec_driver_sql(f'DROP OWNED BY {name}')
        db.exec_driver_sql(f'DROP ROLE {name}')


def test_the_whole_generation_pipeline_runs_under_the_restricted_generator_role(grole):
    lab, name = grole
    engine = role_engine(lab.engine, name)
    prepare(engine, generator=True)                                          # the generator's own startup check passes...
    with pytest.raises(RuntimeError, match='lacks privileges'):
        prepare(engine, generation=True)                                     # ...and the API profile correctly does not
    api = lab.deps()
    request_id = start(lab, api)                                             # the API side reserves and queues
    d = lab.deps(engine=engine)                                              # the generator side does the rest
    assert run(lab, d, 'restricted') == 'done'
    assert view(lab, lab.owner, request_id)['phase'] == 'ready'


@pytest.mark.parametrize('statement', [
    'SELECT * FROM users', 'SELECT * FROM listening_attempts', 'SELECT * FROM listening_responses', 'SELECT * FROM listening_answers', 'SELECT * FROM listening_spoken',
    "UPDATE listening_attempts SET status = 'completed'", "UPDATE listening_spoken SET status = 'assessed'", "UPDATE listening_lessons SET slug = 'x'",
    "UPDATE listening_exercise_rubrics SET data = '{}'::jsonb", "UPDATE listening_lesson_owners SET user_id = gen_random_uuid()",
    'DELETE FROM listening_lessons', 'DELETE FROM listening_generation_requests', 'DELETE FROM listening_lesson_owners', 'DELETE FROM listening_jobs',
    'TRUNCATE listening_lessons', 'CREATE TABLE generator_made_this (id int)', 'ALTER TABLE listening_lessons ADD COLUMN x int'])
def test_everything_outside_the_generators_remit_is_refused(grole, statement):
    lab, name = grole
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        with role_engine(lab.engine, name).begin() as db:
            db.exec_driver_sql(statement)


# ---- a stalled generator must not strand a reservation, and must not break the spoken worker ----------------------------------------

def test_a_generation_job_that_ran_out_of_tries_is_settled_by_the_generator_and_ignored_by_the_spoken_sweep(lab):
    from datetime import timedelta
    from app.listening import spoken
    d = lab.deps()
    request_id = start(lab, d)
    day = utcnow().strftime('%Y-%m-%d')
    assert limits(lab)[day][0] > 0
    with lab.engine.begin() as db:                                           # simulate a generator that claimed the last try and then stalled past its lease
        job = jobs.claim(db, 'w1')
        db.execute(t.jobs.update().where(t.jobs.c.id == job.id).values(attempts=job.max_attempts, lease_expires_at=utcnow() - timedelta(seconds=5)))
    spoken.maintenance(lab.engine, d.objects)                                # the spoken worker's sweep used to raise KeyError('attempt_id') here, every cycle
    with lab.engine.connect() as db:
        assert db.execute(select(t.jobs.c.status)).scalar_one() == 'running'    # and it leaves the generation job to the generator
    assert gj.maintenance(d, ('interpret', 'generate')) == 1
    assert view(lab, lab.owner, request_id)['phase'] == 'failed'
    assert limits(lab)[day][0] == 0                                          # the reservation was given back
