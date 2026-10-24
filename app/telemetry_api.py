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
    'coaching_wait_after_ack', 'longest_pause', 'speech_span']
Milliseconds = Annotated[int, Field(ge=0, le=300000)]


class RequestSpan(StrictModel):
    # AUD-85: one request of a turn as the browser saw it. Fixed kinds, integers only.
    kind: Literal['upload', 'assess', 'ack', 'coach']
    start: Milliseconds       # request start, relative to the detected end of speech
    token: Milliseconds       # time spent obtaining the access token before the request was sent
    ttfb: Milliseconds        # request sent -> response headers


class TurnReport(StrictModel):
    # Elapsed milliseconds per stage. No identifiers, no text, no timestamps.
    stages: dict[ClientStage, Milliseconds] = Field(min_length=1, max_length=len(ClientStage.__args__))
    # AUD-85, only with AUDLI_TURN_TRACE: a random per-turn id (not derived from any account) and the turn's request spans.
    turn: str | None = Field(default=None, pattern=r'^[0-9a-f]{12}$')
    requests: list[RequestSpan] = Field(default_factory=list, max_length=16)


def register_telemetry_routes(app, settings):
    @app.post('/api/telemetry/turn', status_code=204)
    async def turn_report(body: TurnReport):
        # Authenticated like every /api route (middleware), but never touches learner state or the shared lock.
        if settings.latency_aggregates:
            for stage, elapsed in body.stages.items():
                aggregator.record_client(stage, elapsed)
            aggregator.record_client_trace(body.turn, body.requests, body.stages)
        return Response(status_code=204)
