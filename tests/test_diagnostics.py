import logging
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.diagnostics import operation, report_failure
from app.models import Transcription


def test_development_logs_type_message_traceback_operation_but_not_secrets_or_audio(caplog, monkeypatch):
    key = 'test-sensitive-credential'
    monkeypatch.setenv('AUDLI_TEST_SECRET', 'other-sensitive-secret')
    settings = Settings(OPENAI_API_KEY=key, environment='development', _env_file=None)
    with caplog.at_level(logging.ERROR, logger='audli'):
        with pytest.raises(ValueError):
            with operation(settings, 'ExerciseGenerator.generate'):
                raise ValueError("duration mismatch; key=" + key + "; Authorization=Bearer credential\n"
                                 "payload=b'private user audio'; other-sensitive-secret")
    output = caplog.text
    assert 'builtins.ValueError' in output
    assert 'duration mismatch' in output
    assert 'Traceback' in output
    assert 'ExerciseGenerator.generate' in output
    assert 'test_diagnostics.py' in output
    for sensitive in (key, 'Bearer credential', 'private user audio', 'other-sensitive-secret'):
        assert sensitive not in output


def test_production_logs_operation_and_type_only(caplog):
    with caplog.at_level(logging.ERROR, logger='audli'):
        report_failure(Settings(environment='production', persistence='postgres',
            database_url='postgresql://test:test@localhost/test?sslmode=require',
            auth_mode='supabase', supabase_url='https://test.supabase.co',
            supabase_publishable_key='sb_publishable_test', _env_file=None), 'SpeechService.speech', ValueError('private detail'))
    assert 'SpeechService.speech' in caplog.text and 'builtins.ValueError' in caplog.text
    assert 'private detail' not in caplog.text and 'Traceback' not in caplog.text


def test_validation_diagnostics_do_not_dump_input(caplog):
    with caplog.at_level(logging.ERROR, logger='audli'):
        try:
            Transcription.model_validate({'text': {'audio': 'sensitive raw input'}})
        except ValidationError as exc:
            report_failure(Settings(_env_file=None), 'OpenAI.responses.parse', exc)
    assert 'string_type' in caplog.text
    assert 'sensitive raw input' not in caplog.text


def test_http_error_keeps_client_response_generic_while_development_logs_reason(client, caplog):
    client.provider.fail = True
    with caplog.at_level(logging.ERROR, logger='audli'):
        result = client.post('/api/exercises')
    assert result.status_code == 503
    assert 'provider payload' not in result.text
    assert 'SpeechService.speech' in caplog.text
    assert 'Traceback' in caplog.text


def test_named_secret_values_are_redacted_even_when_not_configured(caplog):
    with caplog.at_level(logging.ERROR, logger='audli'):
        report_failure(Settings(_env_file=None), 'OpenAI.responses.parse',
            ValueError('password=temporary-credential; access_token="temporary-token"; sk-proj-***masked-suffix'))
    for value in ('temporary-credential', 'temporary-token', 'masked-suffix'):
        assert value not in caplog.text


def test_literal_validation_diagnostics_do_not_disclose_allowed_learner_quotes(caplog):
    from typing import Literal
    from pydantic import create_model
    model = create_model('PrivateEvidence', evidence=(Literal['private learner statement'], ...))
    with caplog.at_level(logging.ERROR, logger='audli'):
        try:
            model.model_validate({'evidence': 'invented response'})
        except ValidationError as exc:
            report_failure(Settings(_env_file=None), 'ComprehensionEvaluator.evaluate', exc)
    assert 'literal_error' in caplog.text
    assert 'private learner statement' not in caplog.text
    assert 'invented response' not in caplog.text


def test_stage_timings_are_development_only_and_do_not_accept_payloads(caplog):
    with caplog.at_level(logging.INFO):
        with operation(Settings(_env_file=None), 'turn.transcription'):
            private_payload = 'private learner transcript'
        assert 'stage=turn.transcription elapsed_ms=' in caplog.text
        assert private_payload not in caplog.text
        caplog.clear()
        with operation(Settings(environment='production', persistence='postgres',
                database_url='postgresql://test:test@localhost/test?sslmode=require',
                auth_mode='supabase', supabase_url='https://test.supabase.co',
                supabase_publishable_key='sb_publishable_test', _env_file=None), 'turn.transcription'):
            pass
        assert 'Turn timing' not in caplog.text


def test_timed_lock_cancellation_does_not_release_another_operations_lock():
    import asyncio
    from app.diagnostics import timed_lock
    async def scenario():
        lock = asyncio.Lock()
        await lock.acquire()
        async def wait():
            async with timed_lock(Settings(_env_file=None), lock, 'turn.wait'):
                pytest.fail('Cancelled waiter must not acquire the lock')
        task = asyncio.create_task(wait())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert lock.locked()
        lock.release()
        with pytest.raises(ValueError):
            async with timed_lock(Settings(_env_file=None), lock, 'turn.work'):
                raise ValueError('Work failed')
        assert not lock.locked()
    asyncio.run(scenario())


def test_request_timings_are_numeric_request_local_and_reset_on_failure():
    from app.diagnostics import request_timings
    from concurrent.futures import ThreadPoolExecutor
    def scenario(name):
        timings = []
        token = request_timings.set(timings)
        try:
            with pytest.raises(ValueError):
                with operation(Settings(_env_file=None), name):
                    raise ValueError('private transcript')
            assert len(timings) == 1
            assert timings[0][0] == name and timings[0][1] >= 0
            return timings
        finally:
            request_timings.reset(token)
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(scenario, ['turn.transcription', 'turn.tts_provider']))
    assert a is not b
    assert request_timings.get() is None


def test_server_timing_metadata_only_and_per_request(client):
    response = client.post('/api/exercises')
    assert response.status_code == 200
    header = response.headers['Server-Timing']
    assert 'ExerciseGenerator_generate;dur=' in header
    assert 'request_total;dur=' in header
    assert 'Maya' not in header and 'Bearer' not in header
    # The next request cannot inherit provider stages from a previous request.
    assert 'ExerciseGenerator' not in client.get('/api/profile').headers['Server-Timing']
