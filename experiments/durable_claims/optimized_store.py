"""AUD-94 local optimization: batch round trips, retain the original claim protocol.

Two client SQL calls per claim/finalize transaction. Account profile and slot locks
span the read/check/write transaction, never provider work. JSON hashes and operation
records stay compatible with the original PoC. Functions are SECURITY INVOKER.
"""
from datetime import datetime, timedelta
from pathlib import Path
import re

from sqlalchemy import text

from app.models import Conversation, Transcription
from app.upload_idempotency import attempt_id_for
from .store import Store, Claim, Conflict, assert_local, checkpoint, create_schema


def install_functions(engine):
    assert_local(engine)
    create_schema(engine)
    # Explicit opt-in on disposable databases only. No application migration/startup imports.
    with engine.begin() as db:
        db.execute(text(Path(__file__).with_name('optimized_read.sql').read_text()))


def date(value):
    return datetime.fromisoformat(value)


CLAIM_WRITE = text('''
WITH expired AS (
    UPDATE public.poc_upload_operations SET status='cancelled'
    WHERE owner=CAST(:owner AS uuid) AND id=CAST(:old_active AS uuid)
      AND id<>CAST(:id AS uuid) AND status='running'
), slot AS (
    UPDATE public.poc_account_slots SET fence=:fence, active=CAST(:id AS uuid)
    WHERE owner=CAST(:owner AS uuid) AND fence=:old_fence
      AND active IS NOT DISTINCT FROM CAST(:old_active AS uuid)
    RETURNING owner
), operation AS (
    INSERT INTO public.poc_upload_operations(owner,id,exercise,upload_key,fingerprint,checkpoint,fence,status,expires)
    SELECT owner,CAST(:id AS uuid),CAST(:exercise AS uuid),:key,:hash,:checkpoint,:fence,'running',:expires FROM slot
    ON CONFLICT(owner,id) DO UPDATE SET fence=EXCLUDED.fence,status=EXCLUDED.status,expires=EXCLUDED.expires
    RETURNING id
) SELECT count(*) FROM operation
''')

FINAL_WRITE = text('''
WITH attempt AS (
    INSERT INTO public.attempts(id,exercise_id,transcription,recognized_text,confidence,transcription_source,status,created_at)
    VALUES(CAST(:id AS uuid),CAST(:exercise AS uuid),CAST(:transcription AS jsonb),:recognized,:confidence,:source,'transcribed',:now)
    RETURNING id
), checkpoint AS (
    UPDATE public.conversations SET data=CAST(:document AS jsonb),state=:state,
      pending_attempt_id=(SELECT id FROM attempt),updated_at=:now
    WHERE exercise_id=CAST(:exercise AS uuid) AND data=CAST(:expected AS jsonb) AND NOT CAST(:fail AS boolean)
    RETURNING exercise_id
), operation AS (
    UPDATE public.poc_upload_operations SET status='complete'
    WHERE owner=CAST(:owner AS uuid) AND id=CAST(:id AS uuid) AND fence=:fence AND status='running'
      AND EXISTS(SELECT 1 FROM checkpoint)
    RETURNING id
), slot AS (
    UPDATE public.poc_account_slots SET active=NULL
    WHERE owner=CAST(:owner AS uuid) AND active=CAST(:id AS uuid) AND fence=:fence
      AND EXISTS(SELECT 1 FROM operation)
    RETURNING owner
) SELECT (SELECT count(*) FROM attempt),(SELECT count(*) FROM checkpoint),
         (SELECT count(*) FROM operation),(SELECT count(*) FROM slot)
''')


class OptimizedStore(Store):
    def read_locked(self, db, exercise, identifier):
        # SQL function checks the same verified owner and acquires the same locks as
        # Store.locked_slot, then batches its reads. No client owner override exists.
        row = db.execute(text('SELECT public.poc_upload_read(CAST(:owner AS uuid),CAST(:exercise AS uuid),CAST(:id AS uuid))'),
                         dict(owner=self.owner, exercise=exercise, id=identifier)).scalar_one()
        if not row['owned']:
            raise ValueError('Exercise not found')
        row['now'] = self.clock() if self.clock else date(row['now'])
        return row

    @staticmethod
    def question(read):
        if read['has_lesson']:
            raise Conflict('Lesson/check-in protocol is outside this prototype')
        if read['evaluated']:
            raise Conflict('Exercise complete')
        if read['current'] != read['exercise']:
            raise Conflict('Exercise changed')
        row = read['conversation']
        if row is None or row['state'] not in ('AWAITING_SUMMARY', 'AWAITING_FOLLOWUP'):
            raise Conflict('Question not accepting a recording')
        # Keep the original domain schema as the authority for the entire snapshot.
        conversation=Conversation.model_validate(row['data'])
        if (conversation.exercise_id!=read['exercise'] or conversation.state!=row['state']
                or conversation.pending_attempt_id!=row['pending_attempt_id']
                or row['active_followup_id']!=(conversation.active_followup.id if conversation.active_followup else None)):
            raise Conflict('Conversation projections disagree with the checkpoint')
        return row

    @staticmethod
    def canonical(read):
        attempt = read['attempt']
        if attempt is None:
            raise Conflict('Completed operation has no canonical attempt')
        return {'id': attempt['id'], 'transcription': attempt['transcription']}

    def claim(self, exercise, upload_key, fingerprint):
        if not re.fullmatch('[0-9a-f]{16}', upload_key):
            raise Conflict('Stable upload ID required by experiment')
        identifier = attempt_id_for(self.owner, exercise, upload_key)
        with self.repo.transaction() as db:
            read = self.read_locked(db, exercise, identifier)
            slot, op = read['slot'], read['operation']
            if op and op['fingerprint'] != fingerprint:
                raise Conflict('Upload ID reused for different bytes')
            if op and op['status'] == 'complete':
                return Claim('replay', identifier, result=self.canonical(read))
            if op and op['status'] == 'invalidated':
                raise Conflict('Operation invalidated')
            if op is None and read['attempt']:
                return Claim('replay', identifier, result=self.canonical(read))
            active = read['active']
            if active and active['status'] == 'running' and date(active['expires']) > read['now']:
                return Claim('processing' if active['id'] == identifier else 'busy', identifier)
            row = self.question(read)
            if row['pending_attempt_id'] is not None:
                raise Conflict('Existing pending recording must be resolved explicitly')
            stamp = checkpoint(row)
            if op and op['checkpoint'] != stamp:
                raise Conflict('Question changed since first claim')
            fence = slot['fence'] + 1
            changed = db.execute(CLAIM_WRITE,dict(owner=self.owner,id=identifier,exercise=exercise,key=upload_key,
                hash=fingerprint,checkpoint=stamp,fence=fence,expires=read['now']+timedelta(seconds=self.ttl),
                old_fence=slot['fence'],old_active=slot['active'])).scalar_one()
            if changed != 1:
                raise Conflict('Slot changed during claim')
            return Claim('owned',identifier,fence)

    def finalize(self, claim, exercise, transcription: Transcription, *, fail_after_insert=False):
        import json
        with self.repo.transaction() as db:
            read = self.read_locked(db,exercise,claim.operation)
            op, slot = read['operation'], read['slot']
            if op is None or op['exercise'] != exercise:
                raise Conflict('Operation scope mismatch')
            if op['status'] == 'complete':
                return self.canonical(read)
            if (op['status'] != 'running' or op['fence'] != claim.fence or slot['fence'] != claim.fence
                    or slot['active'] != claim.operation or date(op['expires']) <= read['now']):
                raise Conflict('Expired or superseded claim')
            row = self.question(read)
            if checkpoint(row) != op['checkpoint']:
                raise Conflict('Question/checkpoint changed')
            conversation = Conversation.model_validate(row['data'])
            conversation.pending_attempt_id, conversation.pending_text = claim.operation, None
            conversation.state = 'AWAITING_FOLLOWUP' if conversation.active_followup else 'AWAITING_SUMMARY'
            # Only changed checkpoint fields are written. Existing turns/followups/
            # evidence remain byte-for-byte intact, rather than re-upserted.
            changed = tuple(db.execute(FINAL_WRITE,dict(id=claim.operation,exercise=exercise,owner=self.owner,fence=claim.fence,
                transcription=transcription.model_dump_json(),recognized=transcription.text,confidence=transcription.confidence,
                source=transcription.source,now=read['now'],document=conversation.model_dump_json(),
                expected=json.dumps(row['data']),state=conversation.state,fail=fail_after_insert)).one())
            if fail_after_insert:
                raise RuntimeError('injected transaction failure')
            if changed != (1,1,1,1):
                raise Conflict('Atomic finalization did not update every participant')
            return {'id':claim.operation,'transcription':transcription.model_dump(mode='json')}
