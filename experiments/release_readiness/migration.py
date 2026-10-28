"""Disposable rehearsal of a candidate additive migration and its safe rollback.

No production activation. Schema version 8 requires a separately reviewed
startup-compatible bridge; reviewed PR112 accepts only versions through 7.
"""
from pathlib import Path
from sqlalchemy import text,inspect
from app.storage import schema as s
from experiments.durable_claims.store import assert_local,Conflict
from experiments.assessment_claims.schema import install_on,TABLES
from experiments.assessment_claims.security import harden

ROOT=Path(__file__).resolve().parents[2]


def apply_candidate(engine,*,include_review=False,fail_after=None,client_roles=('anon','authenticated','service_role')):
    assert_local(engine)
    import re
    if not all(re.fullmatch('[a-z][a-z0-9_]{1,40}',r) for r in client_roles):raise ValueError('Invalid reviewed role')
    with engine.begin() as db:
        db.exec_driver_sql("SET LOCAL lock_timeout='1s'")
        versions=list(db.execute(text('SELECT version FROM schema_migrations ORDER BY version')).scalars())
        if versions not in [list(range(1,7)),list(range(1,8))]:raise Conflict('Unexpected source schema')
        if versions[-1]==6:
            if not include_review:raise Conflict('PR112 requires reviewed migration007 dependency')
            source=(ROOT/'docs/postgres-007.sql').read_text()
            source=source.replace('\nBEGIN;','',1).rsplit('\nCOMMIT;',1)[0]
            db.exec_driver_sql(source)
        install_on(db)
        # No browser/SQL API access window before commit, including permissive
        # inherited Supabase default ACLs. RLS is defense in depth, not a TRUNCATE gate.
        for name in ('poc_account_slots','poc_upload_operations','poc_stage_operations','review_improvements'):
            db.exec_driver_sql(f'REVOKE ALL ON public.{name} FROM PUBLIC')
            for role in client_roles:
                if db.execute(text('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:r)'),{'r':role}).scalar_one():db.exec_driver_sql(f'REVOKE ALL ON public.{name} FROM "{role}"')
            db.exec_driver_sql(f'ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY')
            db.exec_driver_sql(f'ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY')
        signatures=list(db.execute(text("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard')")).scalars())
        for signature in signatures:
            for role in client_roles:db.exec_driver_sql(f'REVOKE ALL ON FUNCTION {signature} FROM "{role}"')
        db.exec_driver_sql('CREATE INDEX poc_stage_recovery ON public.poc_stage_operations(owner,status,expires)')
        db.exec_driver_sql("CREATE INDEX poc_stage_uncertainty ON public.poc_stage_operations(owner,exercise,(input->>'attempt_id')) WHERE kind='assess' AND status='complete'")
        if fail_after=='ddl':raise Conflict('Injected interrupted migration')
        db.exec_driver_sql('INSERT INTO schema_migrations(version,applied_at) VALUES(8,clock_timestamp())')


def rollback_candidate(engine):
    """Remove only empty experimental objects; never delete live receipts/evidence."""
    assert_local(engine)
    with engine.begin() as db:
        # Existing live legacy/advisory traffic must have been stopped externally.
        db.exec_driver_sql("SET LOCAL lock_timeout='1s'")
        db.exec_driver_sql('LOCK TABLE poc_account_slots,poc_stage_operations,poc_upload_operations IN ACCESS EXCLUSIVE MODE')
        count=db.execute(text('SELECT (SELECT count(*) FROM poc_account_slots)+(SELECT count(*) FROM poc_stage_operations)+(SELECT count(*) FROM poc_upload_operations)')).scalar_one()
        if count:raise Conflict('Populated claims require forward schema retention and guard-aware code rollback')
        for table in TABLES:db.exec_driver_sql(f'DROP TRIGGER IF EXISTS poc_claim_guard ON public."{table.name}"')
        db.exec_driver_sql('DROP TRIGGER IF EXISTS poc_checkpoint_parent_guard ON public.conversations')
        signatures=list(db.execute(text("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard')")).scalars())
        for signature in signatures:db.exec_driver_sql(f'DROP FUNCTION {signature}')
        db.exec_driver_sql('DROP TABLE poc_upload_operations,poc_stage_operations,poc_account_slots')
        db.exec_driver_sql('DELETE FROM schema_migrations WHERE version=8')
        # Migration007 stays additive; do not remove advisory coaching history.


def grant_server(engine,server_role='audli_audit_app'):
    """Rehearse the trusted API role, never grant browser identities this role.

    Tenant authorization remains server-verified request scope; this DB role is
    intentionally an internal multi-tenant actor, not an end-user SQL role.
    """
    import re
    from experiments.assessment_claims.schema import operations
    from experiments.durable_claims.store import slots,operations as upload_operations
    assert_local(engine)
    if not re.fullmatch('[a-z][a-z0-9_]{1,40}',server_role):raise ValueError('Invalid reviewed role')
    with engine.begin() as db:
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{server_role}"')
        for table in [*TABLES,slots,upload_operations,operations]:
            db.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON public."{table.name}" TO "{server_role}"')
            db.exec_driver_sql(f'CREATE POLICY audit_internal_server ON public."{table.name}" TO "{server_role}" USING (true) WITH CHECK (true)')
        signatures=list(db.execute(text("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard')")).scalars())
        for signature in signatures:db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {signature} TO "{server_role}"')
