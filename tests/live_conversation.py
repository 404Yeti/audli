"""Opt-in paid V0.2 service smoke test, excluded from normal discovery.
Original scripted passage and synthetic spoken learner responses; NOT a browser test.
Run: source .venv/bin/activate && pytest -q tests/live_conversation.py -s --tb=no --log-cli-level=ERROR
"""
import asyncio
import httpx
import pytest
from app.feedback import validate_final_feedback
from app.config import Settings
from app.main import create_app
from app.models import ExerciseContent
from app.services.openai_provider import OpenAIProvider


@pytest.mark.parametrize('summary_mode', ['complete', 'sparse'])
def test_live_spoken_evidence_slice(tmp_path, summary_mode):
    settings = Settings()
    if not settings.openai_api_key or settings.provider != 'openai':
        pytest.skip('Live OpenAI configuration unavailable')
    isolated = settings.model_copy(update={'data_dir': tmp_path})
    provider = OpenAIProvider(isolated)
    content = ExerciseContent(title='A phone for work', topic='everyday life',
        script="Maya needed a dependable phone for work, but her budget was limited. At a small shop, she found a refurbished phone. "
               "It had been used before, then repaired and tested. The price was much lower than a brand-new phone. "
               "Before paying, Maya checked that she could make calls and connect to Wi-Fi. She bought it, but kept her old phone "
               "as a backup. The old one was slow, yet it could still make calls if the newer phone stopped working.",
        target_duration_seconds=45, vocabulary_level='B1', speech_rate=.75, information_density=1,
        main_idea='Maya bought a cheaper repaired used phone for work and kept a spare.',
        important_details=['The phone cost less than a new one.', 'She tested calls and Wi-Fi before paying.',
                           'She kept her old phone.'],
        inference_points=['Keeping the spare helps her stay reachable if the new phone fails.'],
        vocabulary_items=[{'phrase': 'refurbished', 'meaning_in_context': 'used, repaired and tested'},
                          {'phrase': 'backup', 'meaning_in_context': 'a spare phone'}],
        questions=[{'question': 'What did Maya choose?', 'expected_information': ['a repaired used phone']}])
    async def original_fixture(profile): return content
    provider.generate = original_fixture  # Isolate evidence/TTS/STT; generation is covered by V0.1 tests.
    app = create_app(isolated, provider)
    summary = ('Maya needed a phone for work. She bought a used phone that had been fixed and tested because it cost less. '
               'She checked calls and Wi-Fi before paying and kept the old phone as a spare.')
    if summary_mode == 'sparse':
        summary = 'Maya needed a dependable phone for work and bought one from a small shop.'
    answers = {'main_idea': summary, 'details': 'It cost less than a new phone. She tested calls and Wi-Fi, and kept the old phone.',
               'vocabulary': 'It means used but repaired and tested. The spare phone was there if she needed it.',
               'inference': 'So she could still be reached for work if the newer phone stopped working.'}
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                generated = await client.post('/api/exercises')
                assert generated.status_code == 200
                exercise = generated.json()
                prefix = f"/api/exercises/{exercise['id']}"
                assert (await client.get(exercise['audio_url'])).status_code == 200
                conversation = (await client.post(prefix+'/conversation/listened')).json()
                for turn_number in range(3):
                    assert (await client.get(prefix+'/transcript')).status_code == 403
                    prompt = await client.post(prefix+'/coach-audio', json={'cue_id': conversation['cue_id']})
                    assert prompt.status_code == 200
                    assert (await client.get(prompt.json()['audio_url'])).status_code == 200
                    followup = conversation['active_followup']
                    spoken = answers[followup['dimension']] if followup else summary
                    learner_audio = await provider.speak(spoken)
                    upload = await client.post(prefix+'/attempts', files={'audio': ('response.mp3', learner_audio, 'audio/mpeg')})
                    assert upload.status_code == 200
                    attempt = upload.json()
                    response = await client.post(f"/api/attempts/{attempt['id']}/assess", json={
                        'text': attempt['transcription']['text'], 'confirmed': True,
                        'followup_id': followup['id'] if followup else None})
                    print('Live evidence turn', turn_number + 1, 'status:', response.status_code, flush=True)
                    assert response.status_code == 200
                    conversation = response.json()
                    if turn_number == 0:
                        assessment = app.state.repository.conversation(exercise['id']).assessment
                        if summary_mode == 'complete':
                            assert all(unit.status == 'demonstrated' for unit in assessment.units if unit.dimension == 'vocabulary')
                        else:
                            assert any(unit.status == 'insufficient_evidence' for unit in assessment.units if unit.dimension == 'vocabulary')
                            assert conversation['active_followup'] is not None and conversation['result'] is None
                    if conversation['result']: break
                assert conversation['result'] and conversation['followups_asked'] <= 2
                validate_final_feedback(conversation['prompt'], settings.max_feedback_words)
                assert conversation['prompt'] == conversation['result']['evaluation']['feedback']
                feedback = await client.post(prefix+'/coach-audio', json={'cue_id': 'feedback'})
                assert feedback.status_code == 200
                assert (await client.get(feedback.json()['audio_url'])).status_code == 200
                assert (await client.get(prefix+'/transcript')).status_code == 200
                event = conversation['result']['adaptation']
                assert sum(event['old_difficulty'][key] != value for key, value in event['new_difficulty'].items()) <= 1
                assert len(app.state.repository.history()) == 1
                print('Live TTS, transcription, evidence assessment, bounded clarification, persistence and feedback audio passed.', flush=True)
    asyncio.run(run())
