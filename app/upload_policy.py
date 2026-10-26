"""AUD-89 P2: the recording-upload retry policy is decided by the server (AUDLI_UPLOAD_RETRY, default OFF).

The browser asks `GET /api/upload-policy` and retries a recording that did not arrive only when the answer says ``enabled``. The
policy therefore has an production kill switch (unset the variable; no browser rebuild) and every failure mode is safe:

* ``AUDLI_UPLOAD_RETRY`` unset, false, or anything that is not clearly true  -> ``{"enabled": false}``;
* ``AUDLI_UPLOAD_RETRY_POLICY`` (optional JSON overrides) malformed, unknown keys, wrong types, out of range -> ``{"enabled": false}``
  and one warning at startup (retries stay OFF rather than running with a policy nobody reviewed);
* the browser treats a missing, failing, malformed or out-of-range answer as disabled as well (web/lib/upload-policy.ts).

Nothing here reads or writes learner state, calls a provider, or depends on the lease.
"""
import json
import logging

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from app.models import StrictModel

_logger = logging.getLogger('uvicorn.error')
TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})


def flag_value(value) -> bool:
    """Only an unambiguous 'true' turns the feature on; a typo can never enable it (or stop the service from starting)."""
    return str(value).strip().lower() in TRUE_VALUES


class UploadPolicy(StrictModel):
    model_config = ConfigDict(extra='forbid', strict=True)        # "8000" or true are mistakes, not numbers: they disable retries
    # Final per-send cap applies AFTER growth; deadline_ms bounds the entire operation in wall-clock time.
    base_ms: int = Field(default=8000, ge=1000, le=60000)               # time allowed for the server's own work (auth, STT, database)
    bytes_per_second: int = Field(default=64000, ge=8000, le=2_000_000)  # slowest link the timeout still tolerates (about 512 kbps)
    min_ms: int = Field(default=9000, ge=2000, le=60000)
    max_ms: int = Field(default=30000, ge=2000, le=120000)
    growth: list[float] = Field(default_factory=lambda: [1.0, 1.25, 1.5], min_length=1, max_length=5)
    max_sends: int = Field(default=3, ge=1, le=5)                        # total sends of one recording, including the first
    deadline_ms: int = Field(default=50000, ge=5000, le=300000)          # total wall-clock time before the learner is told
    backoff_ms: list[int] = Field(default_factory=lambda: [300, 900], max_length=4)
    slow_hint_ms: int = Field(default=3000, ge=500, le=30000)            # when "Sending your answer..." appears

    @field_validator('growth')
    @classmethod
    def growth_in_range(cls, values):
        if any(not 1.0 <= value <= 3.0 for value in values):
            raise ValueError('growth factors must be between 1 and 3')
        return values

    @field_validator('backoff_ms')
    @classmethod
    def backoff_in_range(cls, values):
        if any(not 0 <= value <= 10000 for value in values):
            raise ValueError('backoff must be between 0 and 10000 ms')
        return values

    @model_validator(mode='after')
    def consistent(self):
        if self.min_ms > self.max_ms:
            raise ValueError('min_ms must not exceed max_ms')
        if self.deadline_ms < self.min_ms:
            raise ValueError('deadline_ms must allow at least one minimum-length send')
        return self


DISABLED = {'enabled': False}


def resolve_policy(enabled: bool, overrides: str) -> dict:
    """The public policy document. Never raises."""
    if not enabled:
        return dict(DISABLED)
    try:
        raw = json.loads(overrides) if overrides.strip() else {}
        if not isinstance(raw, dict):
            raise ValueError('overrides must be a JSON object')
        return {'enabled': True, **UploadPolicy.model_validate(raw).model_dump()}
    except (ValueError, ValidationError, TypeError):
        # No value is logged: only that the setting is unusable.
        _logger.warning('AUDLI_UPLOAD_RETRY_POLICY is invalid; recording upload retries stay disabled')
        return dict(DISABLED)


def register_upload_policy_routes(app, policy: dict) -> None:
    @app.get('/api/upload-policy')
    async def upload_policy():
        # Authenticated like every /api route (the middleware), a read-only GET: no lease, no learner state, no provider call.
        return policy
