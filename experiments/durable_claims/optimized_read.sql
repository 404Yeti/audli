-- Local-only batch reader. No SECURITY DEFINER; no dynamic SQL or provider work.
-- Locks remain in the caller's explicit transaction, not inside a separate commit.
CREATE OR REPLACE FUNCTION public.poc_upload_read(p_owner uuid,p_exercise uuid,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_slot public.poc_account_slots%ROWTYPE;
    v_conversation public.conversations%ROWTYPE;
    v_owned boolean;
    v_result jsonb;
BEGIN
    PERFORM user_id FROM public.learner_profiles WHERE user_id=p_owner FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Account unavailable'; END IF;
    INSERT INTO public.poc_account_slots(owner,fence,active) VALUES(p_owner,0,NULL) ON CONFLICT DO NOTHING;
    SELECT * INTO STRICT v_slot FROM public.poc_account_slots WHERE owner=p_owner FOR UPDATE;
    SELECT EXISTS(SELECT 1 FROM public.exercises WHERE id=p_exercise AND user_id=p_owner) INTO v_owned;
    IF NOT v_owned THEN RETURN jsonb_build_object('owned',false); END IF;
    -- Prevent a direct conversation update from racing the checked snapshot/CAS.
    SELECT * INTO v_conversation FROM public.conversations WHERE exercise_id=p_exercise FOR UPDATE;
    SELECT jsonb_build_object(
        'owned',true,'exercise',p_exercise,'slot',to_jsonb(v_slot),'now',clock_timestamp(),
        'operation',(SELECT to_jsonb(o) FROM public.poc_upload_operations o WHERE o.owner=p_owner AND o.id=p_id),
        'active',(SELECT to_jsonb(o) FROM public.poc_upload_operations o WHERE o.owner=p_owner AND o.id=v_slot.active),
        'attempt',(SELECT jsonb_build_object('id',a.id,'transcription',a.transcription)
                   FROM public.attempts a WHERE a.id=p_id AND a.exercise_id=p_exercise),
        'has_lesson',EXISTS(SELECT 1 FROM public.lesson_lifecycles WHERE user_id=p_owner),
        'evaluated',EXISTS(SELECT 1 FROM public.attempts WHERE exercise_id=p_exercise AND status='evaluated'),
        'current',(SELECT id FROM public.exercises WHERE user_id=p_owner ORDER BY created_at DESC,id DESC LIMIT 1),
        'conversation',CASE WHEN v_conversation.exercise_id IS NULL THEN NULL ELSE to_jsonb(v_conversation) END
    ) INTO v_result;
    RETURN v_result;
END $$;
REVOKE ALL ON FUNCTION public.poc_upload_read(uuid,uuid,uuid) FROM PUBLIC;
