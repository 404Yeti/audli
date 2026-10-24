"""A complete synthetic lesson driven over the real HTTP app, as the browser does. Shared by the database budget tests."""
from conftest import FakeProvider  # noqa: F401  (re-exported for callers)
from test_conversation import scripted


def run_lesson(client, audio: bytes, before_ready=None) -> dict:
    """Start, check-in, one exercise with a summary and a follow-up, feedback, ready. Returns the last conversation state."""
    def post(path, **kw):
        response = client.post(path, **kw)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])
        return response.json()

    def step(lesson, verb):
        return post(f"/api/lessons/{lesson['id']}/{verb}", json={'revision': lesson['revision']})

    def respond(lesson):
        uploaded = post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']}, files={'audio': ('response.wav', audio, 'audio/wav')})
        return step(uploaded, 'answer')

    for path in ('/api/profile', '/api/eligibility', '/api/plan', '/api/lessons/current'):
        client.get(path)
    client.post('/api/recognition/acknowledgment-audio', json={'cue': 'assessment'})
    lesson = post('/api/lessons/start')
    lesson = respond(lesson)
    lesson = step(lesson, 'heard')
    if lesson['phase'] == 'REVIEW':
        lesson = respond(lesson); lesson = step(lesson, 'heard')
    lesson = step(lesson, 'heard')
    lesson = step(lesson, 'exercise')
    exercise = lesson['exercise']
    client.get(f"/api/exercises/{exercise['id']}/audio")
    post(f"/api/exercises/{exercise['id']}/conversation/listened")
    scripted(client, [{'inference': 'insufficient_evidence'}, {}])
    followup = None
    for _ in range(3):
        client.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': followup['id'] if followup else 'summary'}, headers={'Accept': 'audio/*'})
        uploaded = post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('response.wav', audio, 'audio/wav')})
        client.post('/api/recognition/acknowledgment-audio', json={'cue': 'assessment'})
        result = post(f"/api/attempts/{uploaded['id']}/assess",
                      json={'text': uploaded['transcription']['text'], 'confirmed': True, 'followup_id': followup['id'] if followup else None})
        if result['state'] != 'AWAITING_FOLLOWUP':
            break
        followup = result['active_followup']
    client.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'}, headers={'Accept': 'audio/*'})
    if before_ready:
        before_ready()
    ready = post(f"/api/exercises/{exercise['id']}/conversation/ready")
    client.get(f"/api/exercises/{exercise['id']}/conversation")
    client.post('/api/telemetry/turn', json={'stages': {'assessment': 5}})
    return ready
