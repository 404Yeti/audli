-- AUD-94 migration008 review candidate. NEVER run at application startup.
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

CREATE TABLE poc_account_slots (
	owner UUID NOT NULL, 
	fence INTEGER NOT NULL, 
	active UUID, 
	PRIMARY KEY (owner), 
	CHECK (fence >= 0), 
	FOREIGN KEY(owner) REFERENCES users (id)
);

CREATE TABLE poc_upload_operations (
	owner UUID NOT NULL, 
	id UUID NOT NULL, 
	exercise UUID NOT NULL, 
	upload_key VARCHAR(16) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	checkpoint VARCHAR(64) NOT NULL, 
	fence INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	expires TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (owner, id), 
	CHECK (status IN ('running','complete','cancelled','invalidated')), 
	FOREIGN KEY(owner) REFERENCES users (id), 
	FOREIGN KEY(exercise) REFERENCES exercises (id)
);

CREATE TABLE poc_stage_operations (
	owner UUID NOT NULL, 
	id UUID NOT NULL, 
	exercise UUID NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	fence INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	expires TIMESTAMP WITH TIME ZONE NOT NULL, 
	snapshot JSONB NOT NULL, 
	input JSONB NOT NULL, 
	PRIMARY KEY (owner, id), 
	CHECK (kind IN ('upload','assess','ready','advance','request')), 
	CHECK (status IN ('running','complete','cancelled','invalidated') AND fence>0), 
	FOREIGN KEY(owner) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE poc_recovery_audit (
	id UUID NOT NULL, 
	owner UUID NOT NULL, 
	operation UUID NOT NULL, 
	actor VARCHAR(63) NOT NULL, 
	session_actor VARCHAR(63) NOT NULL, 
	fence_before INTEGER NOT NULL, 
	fence_after INTEGER NOT NULL, 
	checkpoint VARCHAR(64) NOT NULL, 
	reason VARCHAR(40) NOT NULL, 
	at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner) REFERENCES users (id) ON DELETE CASCADE
);

-- Local reader and guard. All context is transaction-local and all functions INVOKER.
CREATE OR REPLACE FUNCTION public.poc_stage_read(p_owner uuid,p_exercise uuid,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_slot public.poc_account_slots%%ROWTYPE;v_profile public.learner_profiles%%ROWTYPE;
 v_ex public.exercises%%ROWTYPE;v_c public.conversations%%ROWTYPE;v_l public.lesson_lifecycles%%ROWTYPE;
BEGIN
 SELECT * INTO STRICT v_profile FROM public.learner_profiles WHERE user_id=p_owner FOR UPDATE;
 INSERT INTO public.poc_account_slots(owner,fence,active) VALUES(p_owner,0,NULL) ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT v_slot FROM public.poc_account_slots WHERE owner=p_owner FOR UPDATE;
 SELECT * INTO v_ex FROM public.exercises WHERE id=p_exercise AND user_id=p_owner;
 IF v_ex.id IS NULL THEN RAISE EXCEPTION 'Exercise not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO v_l FROM public.lesson_lifecycles WHERE user_id=p_owner ORDER BY started_at DESC LIMIT 1 FOR UPDATE;
 SELECT * INTO v_c FROM public.conversations WHERE exercise_id=p_exercise FOR UPDATE;
 RETURN jsonb_build_object('profile',to_jsonb(v_profile),'exercise',to_jsonb(v_ex),
  'conversation',CASE WHEN v_c.exercise_id IS NULL THEN NULL ELSE to_jsonb(v_c) END,
  'lesson',CASE WHEN v_l.id IS NULL THEN NULL ELSE to_jsonb(v_l) END,
  'slot',to_jsonb(v_slot),'now',clock_timestamp(),
  'session',(SELECT to_jsonb(t) FROM public.training_sessions t WHERE t.id=v_ex.session_id AND t.user_id=p_owner),
  'operation',(SELECT to_jsonb(o) FROM public.poc_stage_operations o WHERE owner=p_owner AND id=p_id),
  'active',(SELECT to_jsonb(o) FROM public.poc_stage_operations o WHERE owner=p_owner AND id=v_slot.active),
  'attempts',coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY created_at,id) FROM public.attempts a WHERE exercise_id=p_exercise),'[]'::jsonb),
  'current',(SELECT id FROM public.exercises WHERE user_id=p_owner ORDER BY created_at DESC,id DESC LIMIT 1),
  'result',(SELECT jsonb_build_object('evaluation',e.data,'adaptation',v.data) FROM public.attempts a JOIN public.evaluation_results e ON e.attempt_id=a.id JOIN public.adaptation_events v ON v.attempt_id=a.id WHERE a.exercise_id=p_exercise AND a.status='evaluated'),
  'work',coalesce((SELECT jsonb_agg(jsonb_build_object('exercise_id',x.id,'evaluation',ev.data,'content',x.content) ORDER BY x.created_at,x.id) FROM public.lesson_exercises le JOIN public.exercises x ON x.id=le.exercise_id JOIN public.attempts a ON a.exercise_id=x.id AND a.status='evaluated' JOIN public.evaluation_results ev ON ev.attempt_id=a.id WHERE le.lesson_id=v_l.id AND le.user_id=p_owner),'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION public.poc_stage_read(uuid,uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.poc_claim_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v jsonb;v_old jsonb;v_owner uuid;v_old_owner uuid;v_slot public.poc_account_slots%%ROWTYPE;
 v_context text;v_fence text;v_id text;
BEGIN
 v=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 v_old=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE v END;
 -- Direct owners are immutable. Related-row owner derives from existing parents.
 IF TG_TABLE_NAME='users' THEN v_owner=(v->>'id')::uuid;v_old_owner=(v_old->>'id')::uuid;
 ELSIF v ? 'user_id' THEN v_owner=(v->>'user_id')::uuid;v_old_owner=(v_old->>'user_id')::uuid;
 ELSIF v ? 'exercise_id' THEN SELECT user_id INTO v_owner FROM public.exercises WHERE id=(v->>'exercise_id')::uuid;
  SELECT user_id INTO v_old_owner FROM public.exercises WHERE id=(v_old->>'exercise_id')::uuid;
 ELSIF v ? 'attempt_id' THEN SELECT x.user_id INTO v_owner FROM public.attempts a JOIN public.exercises x ON x.id=a.exercise_id WHERE a.id=(v->>'attempt_id')::uuid;
  SELECT x.user_id INTO v_old_owner FROM public.attempts a JOIN public.exercises x ON x.id=a.exercise_id WHERE a.id=(v_old->>'attempt_id')::uuid;
 END IF;
 IF v_owner IS NULL OR v_old_owner IS DISTINCT FROM v_owner THEN RAISE EXCEPTION 'Owner change or missing owner' USING ERRCODE='P0001'; END IF;
 -- Check every foreign reference, not just whichever column derived the owner.
 -- This also fences inserts from an unconverted writer whose two parents disagree.
 IF v->>'exercise_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.exercises WHERE id=(v->>'exercise_id')::uuid AND user_id=v_owner) THEN RAISE EXCEPTION 'Exercise parent owner mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'attempt_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.attempts a JOIN public.exercises x ON x.id=a.exercise_id WHERE a.id=(v->>'attempt_id')::uuid AND x.user_id=v_owner AND (v->>'exercise_id' IS NULL OR a.exercise_id=(v->>'exercise_id')::uuid)) THEN RAISE EXCEPTION 'Attempt parent scope mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'lesson_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.lesson_lifecycles WHERE id=(v->>'lesson_id')::uuid AND user_id=v_owner) THEN RAISE EXCEPTION 'Lesson parent owner mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'assignment_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.homework_assignments WHERE id=(v->>'assignment_id')::uuid AND user_id=v_owner) THEN RAISE EXCEPTION 'Homework parent owner mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'session_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.training_sessions WHERE id=(v->>'session_id')::uuid AND user_id=v_owner) THEN RAISE EXCEPTION 'Session parent owner mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'pending_attempt_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.attempts a JOIN public.exercises x ON x.id=a.exercise_id WHERE a.id=(v->>'pending_attempt_id')::uuid AND x.user_id=v_owner AND a.exercise_id=(v->>'exercise_id')::uuid) THEN RAISE EXCEPTION 'Pending parent scope mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'followup_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.conversation_followups f JOIN public.exercises x ON x.id=f.exercise_id WHERE f.id=(v->>'followup_id')::uuid AND x.user_id=v_owner AND f.exercise_id=(v->>'exercise_id')::uuid) THEN RAISE EXCEPTION 'Followup parent scope mismatch' USING ERRCODE='P0001'; END IF;
 IF v->>'active_followup_id' IS NOT NULL AND EXISTS(SELECT 1 FROM public.conversation_followups WHERE id=(v->>'active_followup_id')::uuid) AND NOT EXISTS(SELECT 1 FROM public.conversation_followups f JOIN public.exercises x ON x.id=f.exercise_id WHERE f.id=(v->>'active_followup_id')::uuid AND x.user_id=v_owner AND f.exercise_id=(v->>'exercise_id')::uuid) THEN RAISE EXCEPTION 'Active question parent scope mismatch' USING ERRCODE='P0001'; END IF;

 -- NOWAIT avoids inverted row/profile lock waits from an unconverted direct writer.
 -- A rejection rolls back that writer's entire transaction, rather than waiting in
 -- a lock cycle with finalization. Converted writers lock profile first.
 PERFORM user_id FROM public.learner_profiles WHERE user_id=v_owner FOR UPDATE NOWAIT;
 SELECT * INTO v_slot FROM public.poc_account_slots WHERE owner=v_owner;
 v_context=current_setting('audli.claim_owner',true);v_id=current_setting('audli.claim_id',true);v_fence=current_setting('audli.claim_fence',true);
 IF coalesce(v_id,'')<>'' THEN
  IF v_context IS DISTINCT FROM v_owner::text OR v_slot.active IS DISTINCT FROM v_id::uuid OR v_slot.fence IS DISTINCT FROM v_fence::integer OR NOT EXISTS(SELECT 1 FROM public.poc_stage_operations WHERE owner=v_owner AND id=v_id::uuid AND fence=v_fence::integer AND status='running' AND expires>clock_timestamp()) THEN RAISE EXCEPTION 'Invalid fenced writer' USING ERRCODE='P0001'; END IF;
 ELSIF v_slot.active IS NOT NULL THEN RAISE EXCEPTION 'Account operation in progress' USING ERRCODE='P0001'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
REVOKE ALL ON FUNCTION public.poc_claim_guard() FROM PUBLIC;

-- Legacy write_conversation upserts its JSON before inserting newly issued
-- followups. Preserve that atomic order, but require the final parent at COMMIT.
CREATE OR REPLACE FUNCTION public.poc_checkpoint_parent_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v public.conversations%%ROWTYPE;
BEGIN
 SELECT * INTO v FROM public.conversations WHERE exercise_id=NEW.exercise_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF v.active_followup_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.conversation_followups WHERE id=v.active_followup_id AND exercise_id=v.exercise_id) THEN RAISE EXCEPTION 'Active question parent missing or mismatched' USING ERRCODE='P0001'; END IF;
 IF v.pending_attempt_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.attempts WHERE id=v.pending_attempt_id AND exercise_id=v.exercise_id) THEN RAISE EXCEPTION 'Pending attempt parent mismatched' USING ERRCODE='P0001'; END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.poc_checkpoint_parent_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS poc_checkpoint_parent_guard ON public.conversations;
CREATE CONSTRAINT TRIGGER poc_checkpoint_parent_guard AFTER INSERT OR UPDATE ON public.conversations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.poc_checkpoint_parent_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."users";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."users" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."audio_assets";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."audio_assets" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."learner_profiles";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."learner_profiles" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."learner_roadmaps";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."learner_roadmaps" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."lesson_entitlements";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."lesson_entitlements" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."lesson_lifecycles";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."lesson_lifecycles" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."training_sessions";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."training_sessions" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."exercises";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."exercises" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."homework_assignments";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."homework_assignments" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."attempts";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."attempts" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."coach_audio";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."coach_audio" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."conversation_followups";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."conversation_followups" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."homework_reflections";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."homework_reflections" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."lesson_exercises";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."lesson_exercises" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."adaptation_events";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."adaptation_events" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."conversation_turns";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."conversation_turns" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."conversations";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."conversations" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."evaluation_results";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."evaluation_results" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."review_improvements";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."review_improvements" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."turn_assessments";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."turn_assessments" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

DROP TRIGGER IF EXISTS poc_claim_guard ON public."assessment_evidence";

CREATE TRIGGER poc_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON public."assessment_evidence" FOR EACH ROW EXECUTE FUNCTION public.poc_claim_guard();

CREATE OR REPLACE FUNCTION public.poc_stage_apply(p_owner uuid,p_id uuid,p_fence integer,p_rows jsonb,p_done boolean,p_fail boolean DEFAULT false)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_batch jsonb;v_row jsonb;v_op public.poc_stage_operations%%ROWTYPE;v_slot public.poc_account_slots%%ROWTYPE;
BEGIN
 PERFORM user_id FROM public.learner_profiles WHERE user_id=p_owner FOR UPDATE;
 SELECT * INTO STRICT v_slot FROM public.poc_account_slots WHERE owner=p_owner FOR UPDATE;
 SELECT * INTO STRICT v_op FROM public.poc_stage_operations WHERE owner=p_owner AND id=p_id;
 IF v_slot.active IS DISTINCT FROM p_id OR v_slot.fence<>p_fence OR v_op.fence<>p_fence OR v_op.status<>'running' OR v_op.expires<=clock_timestamp() THEN RAISE EXCEPTION 'Stale operation' USING ERRCODE='P0001'; END IF;
 PERFORM set_config('audli.claim_owner',p_owner::text,true),set_config('audli.claim_id',p_id::text,true),set_config('audli.claim_fence',p_fence::text,true);
 FOR v_batch IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
  FOR v_row IN SELECT value FROM jsonb_array_elements(v_batch->'rows') LOOP
   CASE v_batch->>'table' 
WHEN 'learner_profiles' THEN INSERT INTO public."learner_profiles" ("user_id","data","name","target_language","goal","interests","target_situations","onboarding_status","initial_listening_profile","overall","main_idea","details","vocabulary","inference","natural_speed","speech_rate","duration_seconds","vocabulary_level","information_density","speaker_count","accent_complexity","background_noise","focus","completed_attempts","updated_at") SELECT "user_id","data","name","target_language","goal","interests","target_situations","onboarding_status","initial_listening_profile","overall","main_idea","details","vocabulary","inference","natural_speed","speech_rate","duration_seconds","vocabulary_level","information_density","speaker_count","accent_complexity","background_noise","focus","completed_attempts","updated_at" FROM jsonb_populate_record(NULL::public."learner_profiles",v_row) ON CONFLICT ("user_id") DO UPDATE SET "data"=EXCLUDED."data","name"=EXCLUDED."name","target_language"=EXCLUDED."target_language","goal"=EXCLUDED."goal","interests"=EXCLUDED."interests","target_situations"=EXCLUDED."target_situations","onboarding_status"=EXCLUDED."onboarding_status","initial_listening_profile"=EXCLUDED."initial_listening_profile","overall"=EXCLUDED."overall","main_idea"=EXCLUDED."main_idea","details"=EXCLUDED."details","vocabulary"=EXCLUDED."vocabulary","inference"=EXCLUDED."inference","natural_speed"=EXCLUDED."natural_speed","speech_rate"=EXCLUDED."speech_rate","duration_seconds"=EXCLUDED."duration_seconds","vocabulary_level"=EXCLUDED."vocabulary_level","information_density"=EXCLUDED."information_density","speaker_count"=EXCLUDED."speaker_count","accent_complexity"=EXCLUDED."accent_complexity","background_noise"=EXCLUDED."background_noise","focus"=EXCLUDED."focus","completed_attempts"=EXCLUDED."completed_attempts","updated_at"=EXCLUDED."updated_at";
WHEN 'training_sessions' THEN INSERT INTO public."training_sessions" ("id","user_id","status","started_at","completed_at","starting_difficulty") SELECT "id","user_id","status","started_at","completed_at","starting_difficulty" FROM jsonb_populate_record(NULL::public."training_sessions",v_row) ON CONFLICT ("id") DO UPDATE SET "user_id"=EXCLUDED."user_id","status"=EXCLUDED."status","started_at"=EXCLUDED."started_at","completed_at"=EXCLUDED."completed_at","starting_difficulty"=EXCLUDED."starting_difficulty";
WHEN 'attempts' THEN INSERT INTO public."attempts" ("id","exercise_id","transcription","recognized_text","confidence","transcription_source","confirmed_text","status","created_at") SELECT "id","exercise_id","transcription","recognized_text","confidence","transcription_source","confirmed_text","status","created_at" FROM jsonb_populate_record(NULL::public."attempts",v_row) ON CONFLICT ("id") DO UPDATE SET "exercise_id"=EXCLUDED."exercise_id","transcription"=EXCLUDED."transcription","recognized_text"=EXCLUDED."recognized_text","confidence"=EXCLUDED."confidence","transcription_source"=EXCLUDED."transcription_source","confirmed_text"=EXCLUDED."confirmed_text","status"=EXCLUDED."status","created_at"=EXCLUDED."created_at";
WHEN 'conversations' THEN INSERT INTO public."conversations" ("exercise_id","data","state","pending_attempt_id","active_followup_id","updated_at") SELECT "exercise_id","data","state","pending_attempt_id","active_followup_id","updated_at" FROM jsonb_populate_record(NULL::public."conversations",v_row) ON CONFLICT ("exercise_id") DO UPDATE SET "data"=EXCLUDED."data","state"=EXCLUDED."state","pending_attempt_id"=EXCLUDED."pending_attempt_id","active_followup_id"=EXCLUDED."active_followup_id","updated_at"=EXCLUDED."updated_at";
WHEN 'conversation_followups' THEN INSERT INTO public."conversation_followups" ("id","exercise_id","position","dimension","unit_index","question","created_at") SELECT "id","exercise_id","position","dimension","unit_index","question","created_at" FROM jsonb_populate_record(NULL::public."conversation_followups",v_row) ON CONFLICT ("id") DO UPDATE SET "exercise_id"=EXCLUDED."exercise_id","position"=EXCLUDED."position","dimension"=EXCLUDED."dimension","unit_index"=EXCLUDED."unit_index","question"=EXCLUDED."question","created_at"=EXCLUDED."created_at";
WHEN 'conversation_turns' THEN INSERT INTO public."conversation_turns" ("attempt_id","exercise_id","followup_id","position","confirmed_text","created_at") SELECT "attempt_id","exercise_id","followup_id","position","confirmed_text","created_at" FROM jsonb_populate_record(NULL::public."conversation_turns",v_row) ON CONFLICT ("attempt_id") DO UPDATE SET "exercise_id"=EXCLUDED."exercise_id","followup_id"=EXCLUDED."followup_id","position"=EXCLUDED."position","confirmed_text"=EXCLUDED."confirmed_text","created_at"=EXCLUDED."created_at";
WHEN 'turn_assessments' THEN INSERT INTO public."turn_assessments" ("attempt_id","exercise_id","data","created_at") SELECT "attempt_id","exercise_id","data","created_at" FROM jsonb_populate_record(NULL::public."turn_assessments",v_row) ON CONFLICT ("attempt_id") DO UPDATE SET "exercise_id"=EXCLUDED."exercise_id","data"=EXCLUDED."data","created_at"=EXCLUDED."created_at";
WHEN 'assessment_evidence' THEN INSERT INTO public."assessment_evidence" ("attempt_id","exercise_id","dimension","unit_index","status","evidence","question") SELECT "attempt_id","exercise_id","dimension","unit_index","status","evidence","question" FROM jsonb_populate_record(NULL::public."assessment_evidence",v_row) ON CONFLICT ("attempt_id","dimension","unit_index") DO UPDATE SET "exercise_id"=EXCLUDED."exercise_id","status"=EXCLUDED."status","evidence"=EXCLUDED."evidence","question"=EXCLUDED."question";
WHEN 'evaluation_results' THEN INSERT INTO public."evaluation_results" ("attempt_id","data","version","overall","main_idea","details","vocabulary","inference","coverage","coaching_outcome","created_at") SELECT "attempt_id","data","version","overall","main_idea","details","vocabulary","inference","coverage","coaching_outcome","created_at" FROM jsonb_populate_record(NULL::public."evaluation_results",v_row) ON CONFLICT ("attempt_id") DO UPDATE SET "data"=EXCLUDED."data","version"=EXCLUDED."version","overall"=EXCLUDED."overall","main_idea"=EXCLUDED."main_idea","details"=EXCLUDED."details","vocabulary"=EXCLUDED."vocabulary","inference"=EXCLUDED."inference","coverage"=EXCLUDED."coverage","coaching_outcome"=EXCLUDED."coaching_outcome","created_at"=EXCLUDED."created_at";
WHEN 'adaptation_events' THEN INSERT INTO public."adaptation_events" ("id","attempt_id","data","decision","changed_variable","old_value","new_value","reason","previous_score","policy_version","created_at") SELECT "id","attempt_id","data","decision","changed_variable","old_value","new_value","reason","previous_score","policy_version","created_at" FROM jsonb_populate_record(NULL::public."adaptation_events",v_row) ON CONFLICT ("id") DO UPDATE SET "attempt_id"=EXCLUDED."attempt_id","data"=EXCLUDED."data","decision"=EXCLUDED."decision","changed_variable"=EXCLUDED."changed_variable","old_value"=EXCLUDED."old_value","new_value"=EXCLUDED."new_value","reason"=EXCLUDED."reason","previous_score"=EXCLUDED."previous_score","policy_version"=EXCLUDED."policy_version","created_at"=EXCLUDED."created_at";
WHEN 'lesson_lifecycles' THEN INSERT INTO public."lesson_lifecycles" ("id","user_id","status","revision","started_at","data") SELECT "id","user_id","status","revision","started_at","data" FROM jsonb_populate_record(NULL::public."lesson_lifecycles",v_row) ON CONFLICT ("id") DO UPDATE SET "user_id"=EXCLUDED."user_id","status"=EXCLUDED."status","revision"=EXCLUDED."revision","started_at"=EXCLUDED."started_at","data"=EXCLUDED."data"; ELSE RAISE EXCEPTION 'Unrecognized table'; END CASE;
   IF p_fail THEN RAISE EXCEPTION 'Injected transaction failure'; END IF;
  END LOOP;
 END LOOP;
 IF p_done THEN
  UPDATE public.poc_stage_operations SET status='complete' WHERE owner=p_owner AND id=p_id AND fence=p_fence;
  UPDATE public.poc_account_slots SET active=NULL WHERE owner=p_owner AND fence=p_fence AND active=p_id;
 END IF;
 RETURN true;
END $$; REVOKE ALL ON FUNCTION public.poc_stage_apply(uuid,uuid,integer,jsonb,boolean,boolean) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.poc_stage_claim(p_owner uuid,p_id uuid,p_exercise uuid,p_kind text,p_hash text,p_old integer,p_expires timestamptz,p_snapshot jsonb,p_input jsonb,p_rows jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_slot public.poc_account_slots%%ROWTYPE;
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
END $$; REVOKE ALL ON FUNCTION public.poc_stage_claim(uuid,uuid,uuid,text,text,integer,timestamptz,jsonb,jsonb,jsonb) FROM PUBLIC;

REVOKE ALL ON public.poc_account_slots FROM PUBLIC;

REVOKE ALL ON public.poc_account_slots FROM "anon";

REVOKE ALL ON public.poc_account_slots FROM "authenticated";

REVOKE ALL ON public.poc_account_slots FROM "service_role";

ALTER TABLE public.poc_account_slots ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.poc_account_slots FORCE ROW LEVEL SECURITY;

REVOKE ALL ON public.poc_upload_operations FROM PUBLIC;

REVOKE ALL ON public.poc_upload_operations FROM "anon";

REVOKE ALL ON public.poc_upload_operations FROM "authenticated";

REVOKE ALL ON public.poc_upload_operations FROM "service_role";

ALTER TABLE public.poc_upload_operations ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.poc_upload_operations FORCE ROW LEVEL SECURITY;

REVOKE ALL ON public.poc_stage_operations FROM PUBLIC;

REVOKE ALL ON public.poc_stage_operations FROM "anon";

REVOKE ALL ON public.poc_stage_operations FROM "authenticated";

REVOKE ALL ON public.poc_stage_operations FROM "service_role";

ALTER TABLE public.poc_stage_operations ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.poc_stage_operations FORCE ROW LEVEL SECURITY;

REVOKE ALL ON public.poc_recovery_audit FROM PUBLIC;

REVOKE ALL ON public.poc_recovery_audit FROM "anon";

REVOKE ALL ON public.poc_recovery_audit FROM "authenticated";

REVOKE ALL ON public.poc_recovery_audit FROM "service_role";

ALTER TABLE public.poc_recovery_audit ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.poc_recovery_audit FORCE ROW LEVEL SECURITY;

REVOKE ALL ON public.review_improvements FROM PUBLIC;

REVOKE ALL ON public.review_improvements FROM "anon";

REVOKE ALL ON public.review_improvements FROM "authenticated";

REVOKE ALL ON public.review_improvements FROM "service_role";

ALTER TABLE public.review_improvements ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.review_improvements FORCE ROW LEVEL SECURITY;

REVOKE ALL ON FUNCTION poc_checkpoint_parent_guard() FROM "anon";

REVOKE ALL ON FUNCTION poc_checkpoint_parent_guard() FROM "authenticated";

REVOKE ALL ON FUNCTION poc_checkpoint_parent_guard() FROM "service_role";

REVOKE ALL ON FUNCTION poc_claim_guard() FROM "anon";

REVOKE ALL ON FUNCTION poc_claim_guard() FROM "authenticated";

REVOKE ALL ON FUNCTION poc_claim_guard() FROM "service_role";

REVOKE ALL ON FUNCTION poc_stage_apply(uuid,uuid,integer,jsonb,boolean,boolean) FROM "anon";

REVOKE ALL ON FUNCTION poc_stage_apply(uuid,uuid,integer,jsonb,boolean,boolean) FROM "authenticated";

REVOKE ALL ON FUNCTION poc_stage_apply(uuid,uuid,integer,jsonb,boolean,boolean) FROM "service_role";

REVOKE ALL ON FUNCTION poc_stage_claim(uuid,uuid,uuid,text,text,integer,timestamp with time zone,jsonb,jsonb,jsonb) FROM "anon";

REVOKE ALL ON FUNCTION poc_stage_claim(uuid,uuid,uuid,text,text,integer,timestamp with time zone,jsonb,jsonb,jsonb) FROM "authenticated";

REVOKE ALL ON FUNCTION poc_stage_claim(uuid,uuid,uuid,text,text,integer,timestamp with time zone,jsonb,jsonb,jsonb) FROM "service_role";

REVOKE ALL ON FUNCTION poc_stage_read(uuid,uuid,uuid) FROM "anon";

REVOKE ALL ON FUNCTION poc_stage_read(uuid,uuid,uuid) FROM "authenticated";

REVOKE ALL ON FUNCTION poc_stage_read(uuid,uuid,uuid) FROM "service_role";

CREATE INDEX poc_stage_recovery ON public.poc_stage_operations(owner,status,expires);

CREATE INDEX poc_stage_uncertainty ON public.poc_stage_operations(owner,exercise,(input->>'attempt_id')) WHERE kind='assess' AND status='complete';

INSERT INTO public.schema_migrations(version,applied_at) VALUES(8,clock_timestamp());
COMMIT;
