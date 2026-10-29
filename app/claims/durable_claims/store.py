"""Shared ownership tables and admission to a validated runtime engine."""
from sqlalchemy import Table, Column, String, Integer, DateTime, ForeignKey, CheckConstraint, MetaData
from app.storage import schema as s

metadata = MetaData()
slots = Table('poc_account_slots', metadata,
    Column('owner', s.identifier, ForeignKey(s.users.c.id), primary_key=True),
    Column('fence', Integer, nullable=False),
    Column('active', s.identifier),
    CheckConstraint('fence >= 0'))
operations = Table('poc_upload_operations', metadata,
    Column('owner', s.identifier, ForeignKey(s.users.c.id), primary_key=True),
    Column('id', s.identifier, primary_key=True),
    Column('exercise', s.identifier, ForeignKey(s.exercises.c.id), nullable=False),
    Column('upload_key', String(16), nullable=False),
    Column('fingerprint', String(64), nullable=False),
    Column('checkpoint', String(64), nullable=False),
    Column('fence', Integer, nullable=False),
    Column('status', String(16), nullable=False),
    Column('expires', DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('running','complete','cancelled','invalidated')"))



class Conflict(Exception):
    pass


def assert_local(engine):
    """Allow validated API engines or disposable local test databases only.

    Kept under the reviewed helper name for the shared store dependency closure.
    Schema installers and operator tooling are not part of this runtime package.
    """
    if getattr(engine, '_audli_validated_claim_runtime', False):
        return
    if (engine.dialect.name != 'postgresql' or engine.url.host not in ('127.0.0.1','localhost','::1')
            or not engine.url.database.startswith('audli_claims_')):
        raise ValueError('Ownership requires validated runtime startup')


def claims_only_allowed(*,old_workers,participating_writers):
    if old_workers != 0 or not participating_writers:
        raise Conflict('Claims-only requires drained legacy workers and participating writers')
