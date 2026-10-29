"""Local rehearsal of the proposed internal-table permission boundary."""
import re
from sqlalchemy import text
from experiments.durable_claims.store import assert_local

TABLES=('poc_account_slots','poc_upload_operations','poc_stage_operations')


def harden(engine,server_role):
    assert_local(engine)
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}',server_role):raise ValueError('Invalid reviewed server role')
    with engine.begin() as db:
        roles=set(db.execute(text('SELECT rolname FROM pg_roles')).scalars())
        if server_role not in roles:raise ValueError('Create a non-owner server role for this rehearsal first')
        trusted=db.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:role'),dict(role=server_role)).one()
        if any(trusted):raise ValueError('Server rehearsal role must not bypass RLS')
        for name in TABLES:
            db.exec_driver_sql(f'REVOKE ALL ON TABLE public.{name} FROM PUBLIC')
            for role in ('anon','authenticated','service_role'):
                if role in roles:db.exec_driver_sql(f'REVOKE ALL ON TABLE public.{name} FROM "{role}"')
            db.exec_driver_sql(f'ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY')
            db.exec_driver_sql(f'ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY')
            db.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON public.{name} TO "{server_role}"')
            db.exec_driver_sql(f'DROP POLICY IF EXISTS internal_server ON public.{name}')
            db.exec_driver_sql(f'CREATE POLICY internal_server ON public.{name} TO "{server_role}" USING (true) WITH CHECK (true)')
        # Only explicit server-role function execution. No SECURITY DEFINER bypass.
        functions=db.execute(text("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'poc_%' AND proname NOT LIKE '%guard' ")).scalars()
        for signature in functions:
            db.exec_driver_sql(f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC')
            for role in ('anon','authenticated','service_role'):
                if role in roles:db.exec_driver_sql(f'REVOKE ALL ON FUNCTION {signature} FROM "{role}"')
            db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION {signature} TO "{server_role}"')
