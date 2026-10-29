"""Disposable rehearsal of a candidate additive migration and its safe rollback.

No production activation. Schema version 8 requires a separately reviewed
startup-compatible bridge; reviewed PR112 accepts only versions through 7.
"""
from pathlib import Path
from sqlalchemy import text
from experiments.durable_claims.store import assert_local,Conflict
from experiments.assessment_claims.schema import install_on,TABLES

ROOT=Path(__file__).resolve().parents[2]


def apply_candidate(engine,*,include_review=False,fail_after=None,client_roles=('anon','authenticated','service_role'),server_role=None,recovery_role=None):
    assert_local(engine)
    import re
    if not all(re.fullmatch('[a-z][a-z0-9_]{1,40}',r) for r in client_roles):raise ValueError('Invalid reviewed role')
    with engine.begin() as db:
        db.exec_driver_sql("SET LOCAL lock_timeout='1s'")
        db.exec_driver_sql('LOCK TABLE public.schema_migrations IN EXCLUSIVE MODE')
        versions=list(db.execute(text('SELECT version FROM schema_migrations ORDER BY version')).scalars())
        if versions not in [list(range(1,7)),list(range(1,8))]:raise Conflict('Unexpected source schema')
        existing=db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace AND relname IN ('poc_account_slots','poc_upload_operations','poc_stage_operations','poc_recovery_audit')) OR EXISTS(SELECT 1 FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard'))")).scalar_one()
        if existing:raise Conflict('Unversioned ownership objects require explicit review')
        if versions[-1]==6:
            if not include_review:raise Conflict('PR112 requires reviewed migration007 dependency')
            source=(ROOT/'docs/postgres-007.sql').read_text()
            source=source.replace('\nBEGIN;','',1).rsplit('\nCOMMIT;',1)[0]
            db.exec_driver_sql(source)
        install_on(db)
        # No browser/SQL API access window before commit, including permissive
        # inherited Supabase default ACLs. RLS is defense in depth, not a TRUNCATE gate.
        new_tables=('poc_account_slots','poc_upload_operations','poc_stage_operations','poc_recovery_audit','review_improvements')
        for name in new_tables:
            db.exec_driver_sql(f'REVOKE ALL ON public.{name} FROM PUBLIC')
            for role in client_roles:
                if db.execute(text('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:r)'),{'r':role}).scalar_one():db.exec_driver_sql(f'REVOKE ALL ON public.{name} FROM "{role}"')
            db.exec_driver_sql(f'ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY')
            db.exec_driver_sql(f'ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY')
        # UUID keys create no sequences. Fail closed if a future installer changes
        # that assumption; review sequence USAGE/SELECT separately before release.
        sequences=db.execute(text("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S' AND EXISTS(SELECT 1 FROM pg_depend d JOIN pg_class t ON t.oid=d.refobjid WHERE d.objid=c.oid AND t.relname=ANY(:names))"),{'names':list(new_tables)}).scalars().all()
        if sequences:raise Conflict('New sequence permissions require explicit review')
        signatures=list(db.execute(text("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard')")).scalars())
        for signature in signatures:
            for role in client_roles:db.exec_driver_sql(f'REVOKE ALL ON FUNCTION {signature} FROM "{role}"')
        db.exec_driver_sql('CREATE INDEX poc_stage_recovery ON public.poc_stage_operations(owner,status,expires)')
        db.exec_driver_sql("CREATE INDEX poc_stage_uncertainty ON public.poc_stage_operations(owner,exercise,(input->>'attempt_id')) WHERE kind='assess' AND status='complete'")
        if fail_after=='ddl':raise Conflict('Injected interrupted migration')
        if server_role:grant_server_on(db,server_role)
        if recovery_role:grant_recovery_on(db,recovery_role)
        db.exec_driver_sql('INSERT INTO schema_migrations(version,applied_at) VALUES(8,clock_timestamp())')


def rollback_candidate(engine):
    """Remove only empty experimental objects; never delete live receipts/evidence."""
    assert_local(engine)
    with engine.begin() as db:
        # Existing live legacy/advisory traffic must have been stopped externally.
        db.exec_driver_sql("SET LOCAL lock_timeout='1s'")
        db.exec_driver_sql('LOCK TABLE poc_account_slots,poc_stage_operations,poc_upload_operations,poc_recovery_audit IN ACCESS EXCLUSIVE MODE')
        count=db.execute(text('SELECT (SELECT count(*) FROM poc_account_slots)+(SELECT count(*) FROM poc_stage_operations)+(SELECT count(*) FROM poc_upload_operations)+(SELECT count(*) FROM poc_recovery_audit)')).scalar_one()
        if count:raise Conflict('Populated claims require forward schema retention and guard-aware code rollback')
        for table in TABLES:db.exec_driver_sql(f'DROP TRIGGER IF EXISTS poc_claim_guard ON public."{table.name}"')
        db.exec_driver_sql('DROP TRIGGER IF EXISTS poc_checkpoint_parent_guard ON public.conversations')
        signatures=list(db.execute(text("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard')")).scalars())
        for signature in signatures:db.exec_driver_sql(f'DROP FUNCTION {signature}')
        db.exec_driver_sql('DROP TABLE poc_recovery_audit,poc_upload_operations,poc_stage_operations,poc_account_slots')
        db.exec_driver_sql('DELETE FROM schema_migrations WHERE version=8')
        # Migration007 stays additive; do not remove advisory coaching history.


def grant_server(engine,server_role='audli_audit_app'):
    """Rehearse the trusted API role, never grant browser identities this role.

    Tenant authorization remains server-verified request scope; this DB role is
    intentionally an internal multi-tenant actor, not an end-user SQL role.
    """
    assert_local(engine)
    with engine.begin() as db:
        grant_server_on(db,server_role)


def grant_server_on(db,server_role):
    import re
    from experiments.assessment_claims.schema import operations
    from experiments.durable_claims.store import slots,operations as upload_operations
    if not re.fullmatch('[a-z][a-z0-9_]{1,40}',server_role):raise ValueError('Invalid reviewed role')
    role=db.execute(text('SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication FROM pg_roles WHERE rolname=:r'),{'r':server_role}).first()
    if role is None or any(role):raise Conflict('Backend role must exist and not bypass RLS')
    from app.claims.privileges import runtime_grants
    for statement in runtime_grants(server_role):
        db.exec_driver_sql(statement)


def grant_recovery_on(db,role):
    """Separate pre-provisioned operator role; no membership granted here."""
    if role!='audli_claim_recovery_operator':raise Conflict('Unexpected operator role')
    properties=db.execute(text('SELECT rolsuper,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname=:r'),{'r':role}).first()
    if properties is None or any(properties):raise Conflict('Operator must be a pre-provisioned non-bypass NOLOGIN role')
    db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    for table in TABLES:
        db.exec_driver_sql(f'GRANT SELECT ON public."{table.name}" TO "{role}"')
        db.exec_driver_sql(f'CREATE POLICY recovery_read ON public."{table.name}" FOR SELECT TO "{role}" USING (true)')
    db.exec_driver_sql(f'GRANT UPDATE(user_id) ON public.learner_profiles TO "{role}"') # FOR UPDATE locking only
    db.exec_driver_sql(f'CREATE POLICY recovery_lock ON public.learner_profiles FOR UPDATE TO "{role}" USING (true) WITH CHECK (true)')
    for name in ('poc_stage_operations','poc_account_slots'):
        db.exec_driver_sql(f'GRANT SELECT ON public.{name} TO "{role}"')
        columns='status' if name=='poc_stage_operations' else 'active,fence'
        db.exec_driver_sql(f'GRANT UPDATE({columns}) ON public.{name} TO "{role}"')
        db.exec_driver_sql(f'CREATE POLICY recovery_operator ON public.{name} TO "{role}" USING (true) WITH CHECK (true)')
    db.exec_driver_sql(f'GRANT SELECT,INSERT ON public.poc_recovery_audit TO "{role}"')
    db.exec_driver_sql(f'CREATE POLICY recovery_append ON public.poc_recovery_audit TO "{role}" USING (true) WITH CHECK (true)')
