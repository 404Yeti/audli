"""Read-only local drain certification. It never drops claims or changes flags."""
from sqlalchemy import text
from experiments.durable_claims.store import assert_local,Conflict


def drain_report(engine):
    assert_local(engine)
    with engine.begin() as db:
        # One MVCC statement; no provider calls, no waiting for processing workers.
        row=db.execute(text("""SELECT
          count(*) FILTER(WHERE o.id IS NULL)::integer AS unknown,
          count(*) FILTER(WHERE o.status='running' AND o.expires>clock_timestamp())::integer AS processing,
          count(*) FILTER(WHERE o.id IS NOT NULL AND NOT(o.status='running' AND o.expires>clock_timestamp()))::integer AS unresolved
          FROM poc_account_slots s LEFT JOIN poc_stage_operations o ON o.owner=s.owner AND o.id=s.active
          WHERE s.active IS NOT NULL""")).mappings().one()
        return dict(row)


def require_drained(engine,*,target_protocol):
    # Operator must stop incoming mutations externally before taking this snapshot.
    # A report alone cannot prove that traffic remains stopped afterwards.
    if target_protocol!='guard-aware-v1':raise Conflict('Rollback to an unaware worker is unsafe')
    report=drain_report(engine)
    if any(report.values()):raise Conflict('Active or unknown claims require canonical recovery before rollback')
    return report
