"""Production-shaped public schema and aliased role-permission rehearsal.

Only synthetic rows; managed Auth/Storage and extension event watchers are not
claimed equivalent. Actual application connection role remains a runtime gate.
"""
from contextlib import contextmanager
from pathlib import Path
import json
from sqlalchemy import text
from experiments.durable_claims.local import database
from app.storage import schema as s

ROOT=Path(__file__).resolve().parent
ROLES={'postgres':'audli_audit_owner','anon':'audli_audit_anon','authenticated':'audli_audit_authenticated','service_role':'audli_audit_service'}


def catalogue(db):
    return dict(columns=[tuple(r) for r in db.execute(text("SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"))],
        constraints=[tuple(r) for r in db.execute(text("SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname"))],
        indexes=[tuple(r) for r in db.execute(text("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname"))])


def comparison(engine):
    expected=json.loads((ROOT/'results/production-catalog.json').read_text())
    with engine.connect() as db:actual=catalogue(db)
    want=dict(columns=[(v['table'],v['name'],v['type'],v['not_null'],v['default']) for v in expected['columns']],
        constraints=[(v['table'],v['name'],v['definition']) for v in expected['constraints']],indexes=[(v['table'],v['name'],v['definition']) for v in expected['indexes']])
    return {kind:dict(equal=actual[kind]==want[kind],expected=len(want[kind]),actual=len(actual[kind]),missing=[v for v in want[kind] if v not in actual[kind]],extra=[v for v in actual[kind] if v not in want[kind]]) for kind in want}


def prepare(engine):
    with engine.begin() as db:
        db.exec_driver_sql('DROP TABLE review_improvements')
        db.exec_driver_sql('DELETE FROM schema_migrations WHERE version=7')
        for role,bypass in [('audli_audit_owner',True),('audli_audit_service',True),('audli_audit_anon',False),('audli_audit_authenticated',False),('audli_audit_app',False)]:
            db.execute(text(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN NOSUPERUSER {'BYPASSRLS' if bypass else 'NOBYPASSRLS'}; END IF; END $$"))
            db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
        db.exec_driver_sql('GRANT CREATE ON SCHEMA public TO audli_audit_owner')
        for table in s.metadata.sorted_tables:
            if table.name=='review_improvements':continue
            db.exec_driver_sql(f'ALTER TABLE public."{table.name}" OWNER TO audli_audit_owner')
            db.exec_driver_sql(f'ALTER TABLE public."{table.name}" ENABLE ROW LEVEL SECURITY')
            db.exec_driver_sql(f'REVOKE ALL ON public."{table.name}" FROM PUBLIC,audli_audit_anon,audli_audit_authenticated,audli_audit_service')
            db.exec_driver_sql(f'GRANT TRUNCATE,REFERENCES,TRIGGER,MAINTAIN ON public."{table.name}" TO audli_audit_service')
        # Match postgres-created-table defaults, including the dangerous non-DML
        # grants that ordinary RLS does not restrict.
        db.exec_driver_sql('ALTER DEFAULT PRIVILEGES FOR ROLE audli_audit_owner IN SCHEMA public GRANT TRUNCATE,REFERENCES,TRIGGER,MAINTAIN ON TABLES TO audli_audit_anon,audli_audit_authenticated,audli_audit_service')
        db.exec_driver_sql('ALTER DEFAULT PRIVILEGES FOR ROLE audli_audit_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')
        db.execute(text("CREATE FUNCTION public.audit_auto_rls() RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE c record; BEGIN FOR c IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP IF c.object_type='table' AND c.schema_name='public' THEN EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',c.object_identity); END IF; END LOOP; END $$"))
        db.exec_driver_sql("CREATE EVENT TRIGGER audit_ensure_rls ON ddl_command_end WHEN TAG IN ('CREATE TABLE','CREATE TABLE AS') EXECUTE FUNCTION public.audit_auto_rls()")


@contextmanager
def production_shaped():
    from sqlalchemy import create_engine,event
    with database() as url:
        admin=create_engine(url)
        prepare(admin)
        owner=create_engine(url)
        @event.listens_for(owner,'connect')
        def as_owner(dbapi,record):
            with dbapi.cursor() as cursor:cursor.execute('SET ROLE audli_audit_owner')
            dbapi.commit()
        try:yield url,admin,owner
        finally:owner.dispose();admin.dispose()
