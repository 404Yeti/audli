"""The WHOLE Audli 2.0 system as separate real processes against real PostgreSQL, not isolated unit tests:

  migrations 001-007 + 009-011 (the real opt-in migrator) -> seed natural-audio lessons as a new version (real CLI) -> real `uvicorn` API ->
  real `python -m app.listening.worker` connected as the restricted worker role -> a learner journey over HTTP (real ffmpeg validation of the
  recording, real signed audio with Range) -> background assessment -> recording deleted -> kill switch halts and resumes.

Only the AI provider is the deterministic fake (no network, no paid call). PostgreSQL only (needs AUDLI_TEST_POSTGRES_URL).
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('AUDLI_TEST_POSTGRES_URL'), reason='needs AUDLI_TEST_POSTGRES_URL')
ROOT = Path(__file__).resolve().parents[1]
HEARD = 'I think the restaurant had run out of mushrooms so Maria chose the chicken without onions instead'


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wav_bytes(seconds=3.0) -> bytes:
    import array, io, wave
    samples = array.array('h', (9000 if (i // 40) % 2 else -9000 for i in range(int(16000 * seconds))))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(16000), out.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.fixture(scope='module')
def system(tmp_path_factory):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    from app.config import Settings
    from app.listening.audio_build import build_audio
    from app.listening.domain import LessonContent
    from app.listening.roles import worker_role_sql
    from app.listening.seed import FIXTURES, LESSONS
    from app.listening.speech import FakeSpeech
    from scripts.migrate_postgres import migrate
    tmp = tmp_path_factory.mktemp('system')
    admin = os.environ['AUDLI_TEST_POSTGRES_URL']
    name, role, password = 'audli_sys_' + uuid4().hex, 'lw_' + uuid4().hex[:8], 'pw' + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    owner_url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    learner = str(uuid4())
    settings = Settings(persistence='postgres', database_url=owner_url, learner_id=learner, _env_file=None)
    assert migrate(settings) is True and migrate(settings, listening=True) is True          # the real migrator: 001-007, then 009, 010, 011
    with psycopg.connect(owner_url, autocommit=True) as db:
        db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name), sql.Identifier(role)))
        db.execute(worker_role_sql(role), prepare=False)
    worker_url = make_url(owner_url).set(username=role, password=password).render_as_string(hide_password=False)
    audio_root = tmp / 'audio'
    voices = {'sam': 'a', 'maria': 'b', 'lena': 'c', 'tom': 'd'}
    built = {}
    for lesson in LESSONS:
        result = build_audio(LessonContent.model_validate_json((FIXTURES / lesson / 'lesson.json').read_text()), FakeSpeech(), voices)
        (audio_root / lesson).mkdir(parents=True)
        (audio_root / lesson / 'audio.mp3').write_bytes(result.data)
        (audio_root / lesson / 'audio.json').write_text(json.dumps(result.meta('fake')))
        built[lesson] = result
    base = {k: v for k, v in os.environ.items() if not k.startswith(('AUDLI_', 'OPENAI', 'ELEVENLABS'))}
    base.update(PYTHONPATH=str(ROOT), AUDLI_ENVIRONMENT='development', AUDLI_PERSISTENCE='postgres', AUDLI_AUTH_MODE='local', AUDLI_LEARNER_ID=learner,
                AUDLI_DATA_DIR=str(tmp / 'data'), AUDLI_LISTENING_ENABLED='true', AUDLI_PROVIDER='demo', AUDLI_LISTENING_PROVIDER='fake',
                AUDLI_LISTENING_FAKE_TRANSCRIPT=HEARD, AUDLI_LISTENING_STORAGE='local')
    owner_env, worker_env = {**base, 'AUDLI_DATABASE_URL': owner_url}, {**base, 'AUDLI_DATABASE_URL': worker_url}
    run = lambda *args, env=owner_env: subprocess.run([sys.executable, *args], cwd=tmp, env=env, capture_output=True, text=True, timeout=120)   # cwd=tmp: never reads the repo's .env
    seeded = run('-m', 'scripts.seed_listening_lesson', '--audio-root', str(audio_root), '--revision', '2')
    assert seeded.returncode == 0, seeded.stderr[-800:]
    port, processes = free_port(), []
    api_log, worker_log = (tmp / 'api.log').open('w'), (tmp / 'worker.log').open('w')
    processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=tmp, env=owner_env,
                                      stdout=api_log, stderr=subprocess.STDOUT))
    processes.append(subprocess.Popen([sys.executable, '-m', 'app.listening.worker'], cwd=tmp, env=worker_env, stdout=worker_log, stderr=subprocess.STDOUT))
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=30)
    for _ in range(120):
        try:
            if client.get('/api/auth/config').status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.5)
    else:
        pytest.fail('the API did not start: ' + (tmp / 'api.log').read_text()[-1500:])
    yield SimpleNamespace(client=client, built=built, run=run, owner_env=owner_env, owner_url=owner_url, role=role, tmp=tmp, processes=processes, worker_log=tmp / 'worker.log')
    for process in processes:
        process.terminate()
    for process in processes:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    client.close()
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


from types import SimpleNamespace  # noqa: E402


def lesson_ids(s):
    return {item['slug']: item for item in s.client.get('/api/listening/lessons').json()}


def start(s, slug):
    lesson = lesson_ids(s)[slug]
    view = s.client.post(f"/api/listening/lessons/{lesson['lesson_id']}/attempts", json={'client_request_id': uuid4().hex}).json()
    return view, {e['kind']: e for e in view['exercises']}


def answer(s, view, exercise, **body):
    response = s.client.post(f"/api/listening/attempts/{view['attempt']['id']}/answers", json={'exercise_id': exercise['id'], **body})
    assert response.status_code == 200, response.text
    return response.json()


def send(s, view, exercise, upload_id=None):
    return s.client.post(f"/api/listening/attempts/{view['attempt']['id']}/exercises/{exercise['id']}/recording", params={'upload_id': upload_id or uuid4().hex},
                         content=wav_bytes(), headers={'content-type': 'audio/wav'})


def test_the_processes_are_up_and_the_worker_really_runs_as_the_restricted_role(system):
    import psycopg
    assert all(p.poll() is None for p in system.processes)
    deadline = time.time() + 20
    while time.time() < deadline:
        with psycopg.connect(system.owner_url) as db:
            users = {row[0] for row in db.execute('SELECT usename FROM pg_stat_activity WHERE datname = current_database()').fetchall()}
        if system.role in users:
            break
        time.sleep(0.5)
    assert system.role in users, f'the worker never connected as its restricted role; log: {system.worker_log.read_text()[-800:]}'
    assert 'lacks privileges' not in system.worker_log.read_text()


def test_the_library_serves_the_natural_audio_version_with_measured_cues_and_playable_signed_audio(system):
    items = lesson_ids(system)
    assert list(items) == ['at-the-pharmacy', 'ordering-food-at-a-restaurant'] and all(i['status'] == 'new' and i['duration_ms'] > 30000 for i in items.values())
    view, _ = start(system, 'ordering-food-at-a-restaurant')
    built = system.built['restaurant_b1']
    assert view['lesson']['version_no'] == 1 and view['audio']['cues'] == [c.model_dump() for c in built.cues] and view['audio']['duration_ms'] == built.duration_ms
    signed = system.client.get(f"/api/listening/attempts/{view['attempt']['id']}/audio").json()
    media = system.client.get(signed['url'])
    import hashlib
    assert media.status_code == 200 and hashlib.sha256(media.content).hexdigest() == built.sha256
    assert system.client.get(signed['url'], headers={'Range': 'bytes=10-19'}).status_code == 206


def test_a_full_journey_with_a_background_assessment_through_the_real_stack(system):
    view, ex = start(system, 'at-the-pharmacy')
    answer(system, view, ex['multiple_choice'], option_id='b')
    sent = send(system, view, ex['spoken'])
    assert sent.status_code == 200, sent.text
    assert {e['kind']: e for e in sent.json()['exercises']}['spoken']['spoken']['status'] == 'pending'            # the learner is not made to wait
    answer(system, view, ex['fill_blank'], text='Honey')
    done = answer(system, view, ex['sequencing'], order=['cough', 'allergy', 'dose', 'pay'])
    assert done['attempt']['status'] == 'completed' and done['attempt']['correct'] == 3
    deadline, result = time.time() + 90, None
    while time.time() < deadline:                                                          # the separate worker process finishes it
        spoken = {e['kind']: e for e in system.client.get(f"/api/listening/attempts/{view['attempt']['id']}").json()['exercises']}['spoken']['spoken']
        if spoken['status'] != 'pending':
            result = spoken
            break
        time.sleep(1)
    assert result and result['status'] == 'assessed', f"{result}; worker log: {system.worker_log.read_text()[-1000:]}"
    assert result['transcript'] == HEARD and len(result['points']) == 3
    assert not list((system.tmp / 'data' / 'listening_recordings').rglob('*.wav'))              # the recording is gone from the recording store
    assert 'outcome=done' in system.worker_log.read_text() and HEARD not in system.worker_log.read_text()    # logged by outcome only, never the words
    progress = system.client.get('/api/listening/progress').json()
    assert progress['lessons_completed'] >= 1 and set(progress) == {'lessons_available', 'lessons_completed', 'attempts_completed', 'questions_correct', 'questions_scored', 'accent_counts', 'level_counts', 'recent'}


def test_the_kill_switch_stops_new_spoken_answers_across_processes_and_resume_restores_them(system):
    view, ex = start(system, 'ordering-food-at-a-restaurant')
    answer(system, view, ex['multiple_choice'], option_id='b')
    halted = system.run('-m', 'app.listening.ai_budget', 'halt')
    assert halted.returncode == 0 and json.loads(halted.stdout)['halted'] is True
    refused = send(system, view, ex['spoken'])
    assert refused.status_code == 503 and 'paused' in refused.json()['detail']
    resumed = system.run('-m', 'app.listening.ai_budget', 'resume')
    assert resumed.returncode == 0 and json.loads(resumed.stdout)['halted'] is False
    assert send(system, view, ex['spoken']).status_code == 200


def test_the_restricted_worker_role_cannot_reach_what_it_must_not(system):
    import psycopg
    with psycopg.connect(system.owner_url) as db:
        db.execute(f'SET ROLE {system.role}')
        for statement in ('SELECT * FROM users', 'SELECT * FROM listening_exercise_keys', 'DELETE FROM listening_spoken'):
            with pytest.raises(psycopg.errors.Error):
                db.execute(statement)
            db.rollback()
            db.execute(f'SET ROLE {system.role}')
