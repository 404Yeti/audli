"""AUD-89 P1: a recording sent again must not create a second attempt (default off: AUDLI_UPLOAD_IDEMPOTENCY).

The browser tags each recording with a random 16-hex ``X-Audli-Upload-Id`` that stays with the recording however many times it is
sent. With the flag on, the server derives the attempt's primary key from (learner, exercise, upload id) instead of a random UUID:

* the database primary key is the uniqueness guarantee, on SQLite and PostgreSQL alike, with no lease or in-process lock needed;
* nothing is added to any stored JSON or table, so a rollback to a release without this code reads every row it ever wrote;
* a repeat of the same upload finds the first attempt before any speech-to-text call and returns it unchanged (no second attempt,
  no second transcription, no state transition). Without a valid header, or with the flag off, behaviour is exactly as before.

The id includes the learner, so one learner can never reach or collide with another's attempt, and the exercise, so the same id
sent to a different exercise is simply a new attempt (as it would be today).
"""
import re
import uuid

UPLOAD_ID = re.compile(r'^[0-9a-f]{16}$')
NAMESPACE = uuid.UUID('7d3c4b0e-5f0a-4a52-9a43-1c6f0e2b8d11')     # fixed: derived ids must be stable across releases
HEADER = 'x-audli-upload-id'


def upload_id_from(headers) -> str | None:
    """The upload id when the header is present exactly once and well formed; anything else is treated as no header."""
    values = headers.getlist(HEADER) if hasattr(headers, 'getlist') else [headers[HEADER]] if HEADER in headers else []
    return values[0] if len(values) == 1 and UPLOAD_ID.match(values[0]) else None


def attempt_id_for(learner_id: str, exercise_id: str, upload_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f'{learner_id}|{exercise_id}|{upload_id}'))
