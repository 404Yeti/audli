"""Disposable schema/function/guard installation. Not an application migration."""
from pathlib import Path
from sqlalchemy import MetaData,Table,Column,String,Integer,DateTime,JSON,text,ForeignKey,CheckConstraint
from sqlalchemy.dialects.postgresql import UUID,JSONB
from experiments.durable_claims.store import assert_local,create_schema

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

# Whitelist shared by SQL batch writer and guard installation. Never supplied by HTTP.
from app.storage import schema as s
TABLES=[t for t in s.metadata.sorted_tables if t is s.users or {'user_id','exercise_id','attempt_id'} & set(t.c.keys())]
WRITE_TABLES=[s.profiles,s.sessions,s.attempts,s.conversations,s.followups,s.turns,s.assessments,s.evidence,s.evaluations,s.events,s.lessons]


def install(engine):
    assert_local(engine);create_schema(engine);metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(text(Path(__file__).with_name('functions.sql').read_text()))
        for table in TABLES:
            name=table.name
            db.exec_driver_sql(f'DROP TRIGGER IF EXISTS poc_claim_guard ON public."{name}"')
            db.exec_driver_sql(f'CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."{name}" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard()')
        # Generate fixed per-table upsert branches from repository metadata. No table/
        # column names or SQL fragments can come from request bodies.
        branches=[]
        for table in WRITE_TABLES:
            cols=[c.name for c in table.columns];pk=[c.name for c in table.primary_key.columns]
            quoted=lambda names:','.join('"'+n+'"' for n in names)
            updates=','.join('"'+c+'"=EXCLUDED."'+c+'"' for c in cols if c not in pk)
            branches.append(f"WHEN '{table.name}' THEN INSERT INTO public.\"{table.name}\" ({quoted(cols)}) SELECT {quoted(cols)} FROM jsonb_populate_record(NULL::public.\"{table.name}\",v_row) ON CONFLICT ({quoted(pk)}) DO UPDATE SET {updates};")
        definition='''CREATE OR REPLACE FUNCTION public.poc_stage_apply(p_owner uuid,p_id uuid,p_fence integer,p_rows jsonb,p_done boolean,p_fail boolean DEFAULT false)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_batch jsonb;v_row jsonb;v_op public.poc_stage_operations%ROWTYPE;v_slot public.poc_account_slots%ROWTYPE;
BEGIN
 PERFORM user_id FROM public.learner_profiles WHERE user_id=p_owner FOR UPDATE;
 SELECT * INTO STRICT v_slot FROM public.poc_account_slots WHERE owner=p_owner FOR UPDATE;
 SELECT * INTO STRICT v_op FROM public.poc_stage_operations WHERE owner=p_owner AND id=p_id;
 IF v_slot.active IS DISTINCT FROM p_id OR v_slot.fence<>p_fence OR v_op.fence<>p_fence OR v_op.status<>'running' OR v_op.expires<=clock_timestamp() THEN RAISE EXCEPTION 'Stale operation' USING ERRCODE='P0001'; END IF;
 PERFORM set_config('audli.claim_owner',p_owner::text,true),set_config('audli.claim_id',p_id::text,true),set_config('audli.claim_fence',p_fence::text,true);
 FOR v_batch IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
  FOR v_row IN SELECT value FROM jsonb_array_elements(v_batch->'rows') LOOP
   CASE v_batch->>'table' \n'''+ '\n'.join(branches)+''' ELSE RAISE EXCEPTION 'Unrecognized table'; END CASE;
   IF p_fail THEN RAISE EXCEPTION 'Injected transaction failure'; END IF;
  END LOOP;
 END LOOP;
 IF p_done THEN
  UPDATE public.poc_stage_operations SET status='complete' WHERE owner=p_owner AND id=p_id AND fence=p_fence;
  UPDATE public.poc_account_slots SET active=NULL WHERE owner=p_owner AND fence=p_fence AND active=p_id;
 END IF;
 RETURN true;
END $$; REVOKE ALL ON FUNCTION public.poc_stage_apply(uuid,uuid,integer,jsonb,boolean,boolean) FROM PUBLIC;'''
        db.execute(text(definition))
        db.execute(text('''CREATE OR REPLACE FUNCTION public.poc_stage_claim(p_owner uuid,p_id uuid,p_exercise uuid,p_kind text,p_hash text,p_old integer,p_expires timestamptz,p_snapshot jsonb,p_input jsonb,p_rows jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_slot public.poc_account_slots%ROWTYPE;
BEGIN
 PERFORM user_id FROM public.learner_profiles WHERE user_id=p_owner FOR UPDATE;
 SELECT * INTO STRICT v_slot FROM public.poc_account_slots WHERE owner=p_owner FOR UPDATE;
 IF v_slot.fence<>p_old THEN RAISE EXCEPTION 'Changed ownership' USING ERRCODE='P0001'; END IF;
 IF v_slot.active IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.poc_stage_operations WHERE owner=p_owner AND id=v_slot.active) THEN RAISE EXCEPTION 'Unknown ownership protocol' USING ERRCODE='P0001'; END IF;
 UPDATE public.poc_stage_operations SET status='cancelled' WHERE owner=p_owner AND id=v_slot.active AND id<>p_id AND status='running';
 INSERT INTO public.poc_stage_operations(owner,id,exercise,kind,fingerprint,fence,status,expires,snapshot,input)
 VALUES(p_owner,p_id,p_exercise,p_kind,p_hash,p_old+1,'running',p_expires,p_snapshot,p_input)
 ON CONFLICT(owner,id) DO UPDATE SET fence=p_old+1,status='running',expires=p_expires,snapshot=p_snapshot;
 UPDATE public.poc_account_slots SET fence=p_old+1,active=p_id WHERE owner=p_owner;
 PERFORM public.poc_stage_apply(p_owner,p_id,p_old+1,p_rows,false,false);
 RETURN true;
END $$; REVOKE ALL ON FUNCTION public.poc_stage_claim(uuid,uuid,uuid,text,text,integer,timestamptz,jsonb,jsonb,jsonb) FROM PUBLIC;'''))
