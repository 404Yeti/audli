-- Local reader and guard. All context is transaction-local and all functions INVOKER.
CREATE OR REPLACE FUNCTION public.poc_stage_read(p_owner uuid,p_exercise uuid,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_slot public.poc_account_slots%ROWTYPE;v_profile public.learner_profiles%ROWTYPE;
 v_ex public.exercises%ROWTYPE;v_c public.conversations%ROWTYPE;v_l public.lesson_lifecycles%ROWTYPE;
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
DECLARE v jsonb;v_old jsonb;v_owner uuid;v_old_owner uuid;v_slot public.poc_account_slots%ROWTYPE;
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
DECLARE v public.conversations%ROWTYPE;
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
