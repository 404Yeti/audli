"""Opt-in browser timing reports: fixed stage names and bounded integers only."""
from typing import Annotated, Literal
from fastapi import Response
from pydantic import Field
from app.models import StrictModel
from app.telemetry import aggregator

ClientStage = Literal['silence_detection', 'recording_finalization', 'upload_transcription', 'assessment',
    'checkin_response', 'profile_extraction', 'tts_readiness', 'playback_start', 'turn_to_playback',
    'request_submission', 'response_headers', 'first_audio_byte', 'audio_body_ready',
    'acknowledgment_playback', 'retry_playback', 'acknowledgment_duration', 'coaching_readiness',
    'coaching_wait_after_ack']


class TurnReport(StrictModel):
    # Elapsed milliseconds per stage. No identifiers, no text, no timestamps.
    stages: dict[ClientStage, Annotated[int, Field(ge=0, le=300000)]] = Field(min_length=1, max_length=len(ClientStage.__args__))


def register_telemetry_routes(app, settings):
    @app.post('/api/telemetry/turn', status_code=204)
    async def turn_report(body: TurnReport):
        # Authenticated like every /api route (middleware), but never touches learner state or the shared lock.
        if settings.latency_aggregates:
            for stage, elapsed in body.stages.items():
                aggregator.record('client.' + stage, elapsed)
        return Response(status_code=204)
