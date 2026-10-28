"""Fenced authoritative exercise flow. Explicit local installation, no activation flag."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timedelta
from hashlib import sha256
import json
from uuid import uuid4,uuid5

from fastapi import HTTPException
from sqlalchemy import text,select,update
from app.models import (Conversation,ConversationTurn,ExerciseContent,LearnerProfile,Transcription,EvidenceAssessment)
from app.conversation import validate_evidence,choose_followup,final_evaluation,adapt_final
from app.feedback import final_feedback
from app.lesson import Lesson,public_topic,compose_closing,closing_due
from app.lesson_limit import deadline
from app.recognition import reliable_recognition
from app.storage import schema as s
from app.storage.repository import profile_values,evaluation_values,event_values
from app.upload_idempotency import attempt_id_for,NAMESPACE
from experiments.durable_claims.store import assert_local,Conflict
from .schema import operations


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=lambda v:v.isoformat())


def fingerprint(value):return sha256(encode(value).encode()).hexdigest()


def snapshot(r):
    return dict(profile=r['profile']['data'],exercise={k:r['exercise'][k] for k in ('id','content','difficulty','session_id')},
        session=r['session'],conversation=r['conversation']['data'] if r['conversation'] else None,
        lesson=r['lesson']['data'] if r['lesson'] else None,
        attempts=[{k:a[k] for k in ('id','transcription','confirmed_text','status')} for a in r['attempts']])


@dataclass
class Lease:
    id:str
    fence:int
    exercise:str
    kind:str
    request:dict


class Plan:
    def __init__(self):self.batches=[]
    def row(self,table,value):self.batches.append(dict(table=table.name,rows=[value]));return value


class StageStore:
    def __init__(self,repo,settings,clock=None):
        assert_local(repo.engine)
        self.repo,self.owner,self.settings,self.clock=repo,repo.learner_id,settings,clock

    def read(self,db,exercise,identifier):
        r=db.execute(text('SELECT public.poc_stage_read(CAST(:owner AS uuid),CAST(:ex AS uuid),CAST(:id AS uuid))'),
                     dict(owner=self.owner,ex=exercise,id=identifier)).scalar_one()
        r['now']=self.clock() if self.clock else datetime.fromisoformat(r['now'])
        return r

    def view(self,r):
        c=Conversation.model_validate(r['conversation']['data']) if r['conversation'] else None
        result=r['result'];pending=None
        if c and not result and c.pending_attempt_id:
            a=next((a for a in r['attempts'] if a['id']==c.pending_attempt_id),None)
            if a:pending={k:a[k] for k in ('id','transcription','confirmed_text')}
        state=c.state if c else ('READY_FOR_NEXT' if result else 'LISTENING')
        cue='feedback' if result else c.active_followup.id if c and c.active_followup else 'summary'
        prompt=result['evaluation']['feedback'] if result else "There's one thing I'd like to check. "+c.active_followup.question if c and c.active_followup else 'Tell me what you understood. Use your own words; grammar does not matter.'
        return dict(state=state,cue_id=cue,prompt=prompt,active_followup=c.active_followup.model_dump() if c and c.active_followup else None,
            followups_asked=len(c.followups) if c else 0,pending_attempt=pending,result=result,topic=public_topic(r['exercise']['content']))

    def lesson_view(self,r):
        l=Lesson.model_validate(r['lesson']['data'])
        return dict(id=l.id,revision=l.revision,status=l.status,phase=l.phase,exercise_id=l.exercise_id,
                    exercise={'id':l.exercise_id} if l.exercise_id else None,exercises_completed=l.exercises_completed)

    def question(self,r,allow_closing=False):
        if r['current']!=r['exercise']['id'] or r['result']:raise Conflict('Exercise no longer active')
        c=Conversation.model_validate(r['conversation']['data']) if r['conversation'] else None
        if not c or c.exercise_id!=r['exercise']['id'] or c.state!=r['conversation']['state'] or c.pending_attempt_id!=r['conversation']['pending_attempt_id']:
            raise Conflict('Question checkpoint is inconsistent')
        if r['conversation']['active_followup_id']!=(c.active_followup.id if c.active_followup else None):raise Conflict('Question projection changed')
        l=Lesson.model_validate(r['lesson']['data']) if r['lesson'] else None
        if l and (l.status!='active' or l.phase!='EXERCISE' or l.exercise_id!=r['exercise']['id']):raise Conflict('Lesson moved')
        if self.settings.lesson_limit_mode=='enforce':
            if not l or (not allow_closing and r['now']>=deadline(l)):raise Conflict('Lesson expired')
        return c,l

    def write_conversation(self,plan,r,c):
        row=deepcopy(r['conversation']) if r['conversation'] else dict(exercise_id=c.exercise_id)
        row.update(data=c.model_dump(mode='json'),state=c.state,pending_attempt_id=c.pending_attempt_id,
            active_followup_id=c.active_followup.id if c.active_followup else None,updated_at=r['now'])
        plan.row(s.conversations,row);r['conversation']=row

    def claim(self,exercise,kind,request):
        key=request['upload_id'] if kind=='upload' else fingerprint(request)
        identifier=attempt_id_for(self.owner,exercise,key) if kind=='upload' else str(uuid5(NAMESPACE,f'{self.owner}|{exercise}|{kind}|{key}'))
        digest=fingerprint(request)
        with self.repo.transaction() as db:
            r=self.read(db,exercise,identifier);op=r['operation'];slot=r['slot']
            if op and op['fingerprint']!=digest:raise Conflict('Operation input changed')
            if op and op['status']=='invalidated':raise Conflict('Operation invalidated')
            if kind=='upload':
                a=next((a for a in r['attempts'] if a['id']==identifier),None)
                if a:return 'done',dict(id=a['id'],transcription=a['transcription']),None
            elif kind=='assess':
                c=Conversation.model_validate(r['conversation']['data'])
                if any(t.attempt_id==request['attempt_id'] for t in c.turns):return 'done',self.view(r),None
                if op and op['status']=='complete':return 'uncertain',self.view(r),None
            elif kind=='ready' and r['conversation']['state']=='READY_FOR_NEXT':return 'done',self.view(r),None
            elif kind=='advance' and op and op['status']=='complete':return 'done',self.lesson_view(r),None
            if slot['active']:
                if not r['active']:raise Conflict('Unknown ownership protocol; drain required')
                if r['active']['status']=='running' and datetime.fromisoformat(r['active']['expires'])>r['now']:
                    if r['active']['id']==identifier and r['active']['kind']==kind:
                        return 'processing',{'operation_id':identifier,'kind':kind},None
                    raise Conflict('A different operation owns this account')
            c,l=self.question(r) if kind in ('upload','assess') else (Conversation.model_validate(r['conversation']['data']),Lesson.model_validate(r['lesson']['data']) if r['lesson'] else None)
            if op and op['snapshot']!=snapshot(r):raise Conflict('Operation evidence changed')
            plan=Plan()
            if kind=='upload':
                if c.state not in ('AWAITING_SUMMARY','AWAITING_FOLLOWUP') or c.pending_attempt_id:raise Conflict('Existing pending recording must be resolved')
            elif kind=='assess':
                a=next((a for a in r['attempts'] if a['id']==request['attempt_id']),None)
                if a is None:raise HTTPException(404,'Attempt not found')
                if not request['confirmed'] or not request['text'].strip():raise HTTPException(422,'Confirm what you said first')
                recognized=Transcription.model_validate(a['transcription'])
                if request['hands_free'] and (request['text'].strip()!=recognized.text.strip() or not reliable_recognition(recognized,self.settings.min_transcription_confidence)):
                    return 'uncertain',self.view(r),None
                if request['followup_id']!=(c.active_followup.id if c.active_followup else None) or c.pending_attempt_id!=a['id']:raise Conflict('Use the current question and pending recording')
                if c.state not in ('AWAITING_SUMMARY','AWAITING_FOLLOWUP','ASSESSING','ASSESSING_FOLLOWUP'):raise Conflict('Not accepting assessment')
                c.pending_text=request['text'].strip();c.state='ASSESSING_FOLLOWUP' if c.active_followup else 'ASSESSING'
                a['confirmed_text']=c.pending_text;plan.row(s.attempts,a);self.write_conversation(plan,r,c)
            elif kind=='ready':
                if not r['result'] or c.state not in ('GIVING_FEEDBACK','READY_FOR_NEXT'):raise Conflict('Complete assessment first')
            elif kind=='advance':
                if not l or l.revision!=request['revision'] or l.status!='active' or l.phase!='EXERCISE' or l.exercise_id!=exercise or c.state!='READY_FOR_NEXT' or not r['result']:raise Conflict('Lesson is not ready to advance')
            else:raise Conflict('Unknown operation')
            db.execute(text('SELECT public.poc_stage_claim(CAST(:owner AS uuid),CAST(:id AS uuid),CAST(:ex AS uuid),:kind,:hash,:old,:expires,CAST(:snapshot AS jsonb),CAST(:input AS jsonb),CAST(:rows AS jsonb))'),
                dict(owner=self.owner,id=identifier,ex=exercise,kind=kind,hash=digest,old=slot['fence'],expires=r['now']+timedelta(seconds=60),snapshot=encode(snapshot(r)),input=encode(request),rows=encode(plan.batches)))
            lease=Lease(identifier,slot['fence']+1,exercise,kind,request)
            return 'owned',r,lease

    def finish(self,lease,value=None,fail=False):
        with self.repo.transaction() as db:
            r=self.read(db,lease.exercise,lease.id);op=r['operation'];slot=r['slot']
            if op and op['status']=='complete':
                if lease.kind=='advance':return self.lesson_view(r)
                if lease.kind=='upload':
                    a=next(a for a in r['attempts'] if a['id']==lease.id)
                    return dict(id=a['id'],transcription=a['transcription'])
                if lease.kind=='assess' and not any(t['attempt_id']==lease.request['attempt_id'] for t in r['conversation']['data']['turns']):return {'uncertain':True}
                return self.view(r)
            if (not op or op['kind']!=lease.kind or op['exercise']!=lease.exercise or op['fingerprint']!=fingerprint(lease.request) or op['status']!='running' or op['fence']!=lease.fence or slot['fence']!=lease.fence or slot['active']!=lease.id or datetime.fromisoformat(op['expires'])<=r['now']):raise Conflict('Expired or stale operation')
            if op['snapshot']!=snapshot(r):raise Conflict('Authoritative evidence changed')
            plan=Plan();kind=lease.kind
            if kind=='upload':
                c,l=self.question(r,allow_closing=True);t=Transcription.model_validate(value)
                row=dict(id=lease.id,exercise_id=lease.exercise,transcription=t.model_dump(mode='json'),recognized_text=t.text,confidence=t.confidence,
                    transcription_source=t.source,confirmed_text=None,status='transcribed',created_at=r['now'])
                plan.row(s.attempts,row);r['attempts'].append(row);c.pending_attempt_id=lease.id;c.pending_text=None;self.write_conversation(plan,r,c)
                result=dict(id=lease.id,transcription=t.model_dump(mode='json'))
            elif kind=='assess':
                c,l=self.question(r,allow_closing=True);assessment=EvidenceAssessment.model_validate(value)
                content=ExerciseContent.model_validate(r['exercise']['content'])
                turn=ConversationTurn(attempt_id=lease.request['attempt_id'],text=lease.request['text'].strip(),followup=c.active_followup)
                turns=[*c.turns,turn];validate_evidence(content,assessment,[t.text for t in turns])
                if assessment.transcription_concern:
                    result={'uncertain':True}
                else:
                    c.turns=turns;c.assessment=assessment;c.pending_attempt_id=c.pending_text=None
                    followup=None if self.settings.lesson_limit_mode=='enforce' and l and r['now']>=deadline(l) else choose_followup(c)
                    plan.row(s.turns,dict(attempt_id=turn.attempt_id,exercise_id=lease.exercise,followup_id=turn.followup.id if turn.followup else None,position=len(turns)-1,confirmed_text=turn.text,created_at=r['now']))
                    plan.row(s.assessments,dict(attempt_id=turn.attempt_id,exercise_id=lease.exercise,data=assessment.model_dump(mode='json'),created_at=r['now']))
                    for unit in assessment.units:plan.row(s.evidence,dict(attempt_id=turn.attempt_id,exercise_id=lease.exercise,dimension=unit.dimension,unit_index=unit.index,status=unit.status,evidence=unit.evidence,question=unit.question))
                    if followup:
                        c.followups.append(followup);c.active_followup=followup;c.state='AWAITING_FOLLOWUP'
                        plan.row(s.followups,dict(id=followup.id,exercise_id=lease.exercise,position=len(c.followups)-1,dimension=followup.dimension,unit_index=followup.index,question=followup.question,created_at=r['now']))
                    else:
                        evaluation=final_evaluation(content,assessment)
                        profile= LearnerProfile.model_validate(r['profile']['data'])
                        updated,event=adapt_final(profile,evaluation,self.settings)
                        # Same deterministic policy/feedback as the existing route.
                        from app.lesson import evidence_gaps,evidence_tip
                        advice=[]
                        for item in r['work']:
                            if item['exercise_id']==lease.exercise:break
                            advice.extend(evidence_tip(*gap) for gap in evidence_gaps(item['evaluation'],item['content']))
                        evaluation.feedback=final_feedback(evaluation,event,self.settings.max_feedback_words,content,advice)
                        if updated.completed_attempts!=profile.completed_attempts+1 or event.old_difficulty!=profile.difficulty:raise Conflict('Stale adaptation')
                        if updated.initial_listening_profile is None and not evaluation.insufficient_evidence:updated.initial_listening_profile=updated.listening_profile.model_copy(deep=True)
                        root=next(a for a in r['attempts'] if a['id']==turns[0].attempt_id)
                        if any(a['status']=='evaluated' for a in r['attempts']):raise Conflict('Exercise already evaluated')
                        root.update(status='evaluated',confirmed_text=turns[0].text);plan.row(s.attempts,root)
                        plan.row(s.evaluations,dict(attempt_id=root['id'],**evaluation_values(evaluation)))
                        plan.row(s.events,dict(id=str(uuid4()),attempt_id=root['id'],**event_values(event)))
                        r['profile'].update(profile_values(updated));plan.row(s.profiles,r['profile'])
                        session=r['session']
                        plan.row(s.sessions,{**session,'status':'completed','completed_at':r['now']})
                        c.active_followup=None;c.state='GIVING_FEEDBACK';r['result']=dict(evaluation=evaluation.model_dump(mode='json'),adaptation=event.model_dump(mode='json'))
                    self.write_conversation(plan,r,c);result=self.view(r)
            elif kind=='ready':
                c=Conversation.model_validate(r['conversation']['data']);c.state='READY_FOR_NEXT';self.write_conversation(plan,r,c);result=self.view(r)
            elif kind=='advance':
                l=Lesson.model_validate(r['lesson']['data']);l.exercises_completed=len(r['work']);l.exercise_id=None
                if closing_due(l,r['now']):
                    compose_closing(l,[i['evaluation'] for i in r['work']],[i['content']['topic'] for i in r['work']],[i['content'] for i in r['work']]);l.phase='CLOSING'
                l.revision+=1;r['lesson'].update(data=l.model_dump(mode='json'),revision=l.revision,status=l.status);plan.row(s.lessons,r['lesson']);result=self.lesson_view(r)
            else:raise Conflict('Unknown completion')
            db.execute(text('SELECT public.poc_stage_apply(CAST(:owner AS uuid),CAST(:id AS uuid),:fence,CAST(:rows AS jsonb),true,:fail)'),
                dict(owner=self.owner,id=lease.id,fence=lease.fence,rows=encode(plan.batches),fail=fail))
            return result

    def abandon(self,lease):
        with self.repo.transaction() as db:
            r=self.read(db,lease.exercise,lease.id)
            if r['slot']['active']==lease.id and r['slot']['fence']==lease.fence:
                db.execute(update(operations).where(operations.c.owner==self.owner,operations.c.id==lease.id).values(status='cancelled'))
                db.execute(text('UPDATE poc_account_slots SET active=NULL WHERE owner=CAST(:owner AS uuid)'),dict(owner=self.owner))

    def invalidate(self,exercise,action='reset',expected_revision=None,expected_pending=None,expected_followup=None):
        # Known conflicting writers invalidate in the SAME transaction as mutation.
        with self.repo.transaction() as db:
            r=self.read(db,exercise,str(uuid4()))
            if action=='record_again':
                c=Conversation.model_validate(r['conversation']['data'])
                if c.pending_attempt_id!=expected_pending or (c.active_followup.id if c.active_followup else None)!=expected_followup or any(t.attempt_id==expected_pending for t in c.turns):raise Conflict('Pending recording changed')
            db.execute(update(operations).where(operations.c.owner==self.owner,operations.c.id==r['slot']['active']).values(status='invalidated'))
            db.execute(text('UPDATE poc_account_slots SET active=NULL,fence=fence+1 WHERE owner=CAST(:owner AS uuid)'),dict(owner=self.owner))
            if action in ('pause','expire'):
                if not r['lesson']:raise Conflict('No lesson')
                l=Lesson.model_validate(r['lesson']['data'])
                if action=='expire':self.repo_bound(db).expire_lesson(l.id,r['now'])
                else:
                    from app.lesson import elapsed
                    l.elapsed_seconds=elapsed(l,r['now']);l.active_since=None;l.status='paused'
                    self.repo_bound(db).save_lesson(l,l.revision)
            elif action=='record_again':
                c.pending_attempt_id=c.pending_text=None;c.state='AWAITING_FOLLOWUP' if c.active_followup else 'AWAITING_SUMMARY'
                self.repo_bound(db).save_conversation(c)
            elif action=='reset':
                # Preserve captured evidence on reset; revoke its use for progression.
                c=Conversation.model_validate(r['conversation']['data']);c.state='LISTENING'
                self.repo_bound(db).save_conversation(c)
            elif action=='onboarding':
                from app.onboarding_reset import reset_onboarding
                from app.limits import LessonLimits
                LessonLimits(self.repo_bound(db),self.settings,clock=lambda:r['now']).block_onboarding_restart()
                # Explicit reset retains original discard semantics and deletes the
                # local experiment's FK-free operation tombstones only on account removal.
                reset_onboarding(self.repo_bound(db),expected_revision if expected_revision is not None else r['profile']['data']['onboarding']['revision'])
            else:raise Conflict('Unknown invalidating action')

    def repo_bound(self,db):
        from contextlib import contextmanager
        from app.storage.repository import SQLProgressRepository
        class Bound(SQLProgressRepository):
            @contextmanager
            def transaction(self,**unused):yield db
            @contextmanager
            def read(self):yield db
        bound=Bound(self.repo.engine,self.owner)
        bound.limit_mode=self.repo.limit_mode;bound.require_auth_user=self.repo.require_auth_user
        return bound

    def settle(self,exercise):
        from app.lesson_limit import window_phase
        if self.settings.lesson_limit_mode!='enforce':return
        with self.repo.transaction() as db:
            r=self.read(db,exercise,str(uuid4()))
            if not r['lesson']:return
            l=Lesson.model_validate(r['lesson']['data'])
            if l.status not in ('active','paused'):return
            phase=window_phase(l,r['now'])
            if phase=='open' or (phase=='closing' and (r['result'] or l.phase in ('CLOSING','COMPLETED'))):return
            db.execute(update(operations).where(operations.c.owner==self.owner,operations.c.id==r['slot']['active']).values(status='invalidated'))
            db.execute(text('UPDATE poc_account_slots SET active=NULL,fence=fence+1 WHERE owner=CAST(:owner AS uuid)'),dict(owner=self.owner))
            bound=self.repo_bound(db)
            if phase=='over':bound.expire_lesson(l.id,r['now'])
            else:
                compose_closing(l,[i['evaluation'] for i in r['work']],[i['content']['topic'] for i in r['work']],[i['content'] for i in r['work']])
                l.phase='CLOSING';l.exercise_id=None;bound.save_lesson(l,l.revision)
