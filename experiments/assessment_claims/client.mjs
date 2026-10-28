// Real P2 sender, unchanged policy and stable IDs; mocked real HTTP backend.
import {pathToFileURL} from 'node:url';
import {readFile} from 'node:fs/promises';
const spec=JSON.parse(await readFile(process.argv[2],'utf8'));
if(!spec.base.startsWith('http://127.0.0.1:'))throw new Error('Loopback required');
const {deliver}=await import(pathToFileURL(`${spec.root}/web/lib/upload-sender.ts`));
const {parseUploadPolicy}=await import(pathToFileURL(`${spec.root}/web/lib/upload-policy.ts`));
const {ApiError}=await import(pathToFileURL(`${spec.root}/web/lib/api.ts`));
const bytes=await readFile(spec.audio);
const rows=await Promise.all(spec.learners.map(async(row,index)=>{
 const headers={Authorization:`Bearer ${row.token}`,'X-Poc-Index':String(index)};
 let sends=0,assessRequests=0;const sendsByRecording=[];const stages={upload_ms:[],assessment_ms:[]};const start=performance.now();
 const request=async(path,init={})=>{
  const response=await fetch(spec.base+path,{...init,headers:{...headers,...init.headers}});
  if(!response.ok)throw new ApiError('Synthetic request failed',response.status);
  return {status:response.status,value:await response.json()};
 };
 try{
  let question=null;
  for(let turn=0;turn<2;turn++){
   const t=performance.now();sendsByRecording.push(0);
   const policy=parseUploadPolicy((await request('/api/upload-policy')).value);
   if(!policy.enabled)throw new Error('Local retry policy disabled');
   const key=`${spec.wave.toString(16).padStart(8,'0')}${turn.toString(16).padStart(4,'0')}${index.toString(16).padStart(4,'0')}`;
   const uploaded=await deliver({policy:{...policy,deadlineMs:policy.deadlineMs-(performance.now()-t)},bytes:bytes.length,
    canRetry:async signal=>parseUploadPolicy((await request('/api/upload-policy',{signal})).value).enabled,
    attempt:async signal=>{
     sends++;sendsByRecording[turn]++;const form=new FormData();form.append('audio',new Blob([bytes],{type:'audio/wav'}),'response.wav');
     let response=await request(`/api/exercises/${row.exercise}/attempts`,{method:'POST',body:form,headers:{'X-Audli-Upload-Id':key},signal});
     if(response.status===202){
      while(true){
       const state=await request(`/api/exercises/${row.exercise}/conversation`,{signal});
       if(state.value.pending_attempt?.id===response.value.operation_id)return state.value.pending_attempt;
       await new Promise(resolve=>setTimeout(resolve,250));signal.throwIfAborted();
      }
     }
     return response.value;
    }});
   stages.upload_ms.push(performance.now()-t);
   const a=performance.now();assessRequests++;
   let response=await request(`/api/attempts/${uploaded.value.id}/assess`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:uploaded.value.transcription.text,confirmed:true,hands_free:true,followup_id:question?.id??null})});
   while(response.status===202||response.value.state==='ASSESSING'||response.value.state==='ASSESSING_FOLLOWUP'){
    await new Promise(resolve=>setTimeout(resolve,250));
    response=await request(`/api/exercises/${row.exercise}/conversation`);
   }
   stages.assessment_ms.push(performance.now()-a);question=response.value.active_followup;
   if(turn===0&&response.value.state!=='AWAITING_FOLLOWUP')throw new Error('Missing followup');
   if(turn===1&&response.value.state!=='GIVING_FEEDBACK')throw new Error('Missing final assessment');
  }
  const r=performance.now();await request(`/api/exercises/${row.exercise}/conversation/ready`,{method:'POST'});
  const advanced=await request(`/api/lessons/${row.lesson}/advance`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:row.revision})});
  const value=advanced.value;
  if(value.exercises_completed!==1||(value.exercise_id??value.exercise?.id??null)!==null)throw new Error('Progression not persisted');
  return {ok:true,ms:performance.now()-start,sends,assessRequests,sendsByRecording,...stages,progression_ms:performance.now()-r};
 }catch{return {ok:false,ms:performance.now()-start,sends,assessRequests,sendsByRecording,...stages};}
}));
console.log(JSON.stringify(rows));
