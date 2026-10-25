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


class UploadSend(StrictModel):
    # AUD-89: one send of one recording as the browser saw it. Enums, bounded integers and two short sanitized strings; never
    # audio, text, URLs or identifiers. ``id`` is random per recording (not derived from any account).
    id: str = Field(pattern=r'^[0-9a-f]{16}$')
    n: int = Field(ge=1, le=9)                           # which send of this recording (a retry of the same audio is 2, 3, ...)
    kind: Literal['exercise', 'lesson', 'plan', 'onboarding', 'homework']
    outcome: Literal['ok', 'network', 'timeout', 'aborted', 'http']
    status: int = Field(ge=0, le=599)                    # 0 when no response arrived
    ms: Milliseconds                                     # send -> response headers or failure
    bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    mime: Literal['webm', 'mp4', 'ogg', 'wav', 'mp3', 'm4a', 'other']
    rec_ms: Milliseconds | None = None                   # length of the recording itself
    since_prev_ms: Milliseconds | None = None            # quiet time on this page since the previous API request finished
    online: bool | None = None
    net: Literal['slow-2g', '2g', '3g', '4g', 'unknown'] = 'unknown'
    rtt: int | None = Field(default=None, ge=0, le=10000)
    downlink_kbps: int | None = Field(default=None, ge=0, le=1000000)
    hidden: int = Field(default=0, ge=0, le=20)          # times the page was hidden during the send
    hidden_ms: Milliseconds = 0
    pagehide: bool = False
    errname: str = Field(default='', max_length=40, pattern=r'^[A-Za-z]*$')
    err: str = Field(default='', max_length=60, pattern=r"^[A-Za-z .,'-]*$")


class TurnReport(StrictModel):
    # Elapsed milliseconds per stage. No identifiers, no text, no timestamps.
    stages: dict[ClientStage, Milliseconds] = Field(min_length=1, max_length=len(ClientStage.__args__))
    # AUD-85, only with AUDLI_TURN_TRACE: a random per-turn id (not derived from any account) and the turn's request spans.
    turn: str | None = Field(default=None, pattern=r'^[0-9a-f]{12}$')
    requests: list[RequestSpan] = Field(default_factory=list, max_length=16)
    # AUD-89 P0: per-send upload records (only with AUDLI_TURN_TRACE logging); see UploadSend.
    deliveries: list[UploadSend] = Field(default_factory=list, max_length=8)


def register_telemetry_routes(app, settings):
    @app.post('/api/telemetry/turn', status_code=204)
    async def turn_report(body: TurnReport):
        # Authenticated like every /api route (middleware), but never touches learner state or the shared lock.
        if settings.latency_aggregates:
            for stage, elapsed in body.stages.items():
                aggregator.record_client(stage, elapsed)
            for send in body.deliveries:
                aggregator.record('client.upload_send_' + send.outcome, send.ms)
            aggregator.record_client_trace(body.turn, body.requests, body.stages, body.deliveries)
        return Response(status_code=204)
