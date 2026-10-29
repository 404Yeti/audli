"""Disposable schema/function/guard installation. Not an application migration."""
from pathlib import Path
from sqlalchemy import MetaData,Table,Column,String,Integer,DateTime,JSON,text,ForeignKey,CheckConstraint
from sqlalchemy.dialects.postgresql import UUID,JSONB
from app.claims.durable_claims.store import assert_local

from app.storage import schema as s
metadata=MetaData()
operations=Table('poc_stage_operations',metadata,
    Column('owner',UUID(as_uuid=False),ForeignKey(s.users.c.id,ondelete='CASCADE'),primary_key=True),Column('id',UUID(as_uuid=False),primary_key=True),
    Column('exercise',UUID(as_uuid=False),nullable=False),Column('kind',String(20),nullable=False),
    Column('fingerprint',String(64),nullable=False),Column('fence',Integer,nullable=False),
    Column('status',String(16),nullable=False),Column('expires',DateTime(timezone=True),nullable=False),
    Column('snapshot',JSONB,nullable=False),Column('input',JSONB,nullable=False),
    CheckConstraint("kind IN ('upload','assess','ready','advance','request')"),
    CheckConstraint("status IN ('running','complete','cancelled','invalidated') AND fence>0"))

# Owner-bound recovery evidence, erased with the account. No audio/transcript/body.
recovery_audit=Table('poc_recovery_audit',metadata,
    Column('id',UUID(as_uuid=False),primary_key=True),
    Column('owner',UUID(as_uuid=False),ForeignKey(s.users.c.id,ondelete='CASCADE'),nullable=False),
    Column('operation',UUID(as_uuid=False),nullable=False),
    Column('actor',String(63),nullable=False),
    Column('session_actor',String(63),nullable=False),
    Column('fence_before',Integer,nullable=False),Column('fence_after',Integer,nullable=False),
    Column('checkpoint',String(64),nullable=False),
    Column('reason',String(40),nullable=False),Column('at',DateTime(timezone=True),nullable=False))

# Whitelist shared by SQL batch writer and guard installation. Never supplied by HTTP.
from app.storage import schema as s
TABLES=[t for t in s.metadata.sorted_tables if t is s.users or {'user_id','exercise_id','attempt_id'} & set(t.c.keys())]
WRITE_TABLES=[s.profiles,s.sessions,s.attempts,s.conversations,s.followups,s.turns,s.assessments,s.evidence,s.evaluations,s.events,s.lessons]
