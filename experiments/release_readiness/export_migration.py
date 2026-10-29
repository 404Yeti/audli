"""Generate review-only numbered SQL from a disposable production-shaped schema."""
from pathlib import Path
from sqlalchemy import event,text
from .rehearsal import production_shaped
from .migration import apply_candidate

ROOT=Path(__file__).resolve().parents[2]


def build():
    captured=[]
    with production_shaped() as (_,admin,owner):
        seven=(ROOT/'docs/postgres-007.sql').read_text().replace('\nBEGIN;','',1).rsplit('\nCOMMIT;',1)[0]
        with owner.begin() as db:db.exec_driver_sql(seven)
        @event.listens_for(owner,'before_cursor_execute')
        def capture(db,cursor,statement,parameters,context,many):
            import re
            first=re.sub(r'(?m)^\s*--[^\n]*(?:\n|$)','',statement).lstrip().split(None,1)[0].upper()
            if first in ('CREATE','ALTER','DROP','REVOKE','GRANT'):
                assert not parameters
                captured.append(statement.strip().rstrip(';')+';')
        apply_candidate(owner,client_roles=('audli_audit_anon','audli_audit_authenticated','audli_audit_service'))
    source='\n'.join(line.rstrip() for line in '\n\n'.join(captured).splitlines())
    for before,after in [('audli_audit_anon','anon'),('audli_audit_authenticated','authenticated'),('audli_audit_service','service_role')]:
        source=source.replace('"'+before+'"','"'+after+'"')
    return """-- AUD-94 migration008 review candidate. NEVER run at application startup.
-- Requires explicit migration007 and schema-compatible guard-aware workers.
-- Backend/operator grants require a reviewed real role; this artifact grants
-- no new runtime identity or membership. See P0-REMEDIATION.md.
BEGIN;
SET LOCAL lock_timeout='1s';
SET LOCAL statement_timeout='5s';
LOCK TABLE public.schema_migrations IN EXCLUSIVE MODE;
DO $$ BEGIN
 IF ARRAY(SELECT version FROM public.schema_migrations ORDER BY version) <> ARRAY[1,2,3,4,5,6,7] THEN
  RAISE EXCEPTION 'Expected explicit migrations001 through007';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace AND relname IN ('poc_account_slots','poc_upload_operations','poc_stage_operations','poc_recovery_audit'))
 OR EXISTS(SELECT 1 FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('poc_stage_claim','poc_stage_apply','poc_stage_read','poc_claim_guard','poc_checkpoint_parent_guard')) THEN
  RAISE EXCEPTION 'Unversioned ownership objects require explicit review';
 END IF;
END $$;

"""+source+"\n\nINSERT INTO public.schema_migrations(version,applied_at) VALUES(8,clock_timestamp());\nCOMMIT;\n"


if __name__=='__main__':
    (ROOT/'docs/postgres-008.sql').write_text(build())
