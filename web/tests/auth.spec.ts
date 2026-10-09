import { test, expect, type Page } from '@playwright/test';
import { ExerciseLessonFixture } from './lesson-fixture';
import { installVoice, promptWav, probe, endAudio, type Probe } from './voice-fixture';

const userId='00000000-0000-0000-0000-000000000001';
const user={id:userId,email:'learner@example.com',email_confirmed_at:'2026-10-06T00:00:00Z',app_metadata:{provider:'email',providers:['email']},user_metadata:{},aud:'authenticated',created_at:'2026-10-06T00:00:00Z'};
const userB={...user,id:'00000000-0000-0000-0000-000000000002',email:'bob@example.com'};
const tokenFor=(id:string)=>[Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url'),Buffer.from(JSON.stringify({sub:id,exp:Math.floor(Date.now()/1000)+3600,aud:'authenticated',role:'authenticated'})).toString('base64url'),'mock-signature'].join('.');
const token=tokenFor(userId),tokenB=tokenFor(userB.id);
type Request={path:string;authorization:string|undefined;method:string;body:string|null};
async function setupAuth(page:Page,training=false){
  const requests:Request[]=[];let reject=false,listened=false;
  await page.route('https://audli-auth-test.supabase.co/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/signup'))return route.fulfill({json:user});
    if(path.endsWith('/logout'))return route.fulfill({status:204});
    if(path.endsWith('/token')){const second=route.request().postDataJSON()?.email===userB.email;return route.fulfill({json:{access_token:second?tokenB:token,refresh_token:'mock-refresh-token',expires_in:3600,token_type:'bearer',user:second?userB:user}});}
    return route.fulfill({json:user});
  });
  let generated=false;const clip=()=>({id:'clip',audio_url:'/api/exercises/clip/audio',completed_attempt_id:null});
  const lesson=new ExerciseLessonFixture(page,()=>training||generated?clip():null,()=>{generated=true;return clip();});
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname,authorization=route.request().headers()['authorization'];
    if(path==='/api/auth/config')return route.fulfill({json:{mode:'supabase'}});
    requests.push({path,authorization,method:route.request().method(),body:route.request().postData()});
    if(reject||![`Bearer ${token}`,`Bearer ${tokenB}`].includes(authorization??''))return route.fulfill({status:401,json:{detail:'Your session expired or is invalid. Please sign in again.'}});
    if(path.startsWith('/api/lessons/') && authorization===`Bearer ${tokenB}` && path.endsWith('/current'))return route.fulfill({json:null});
    if(await lesson.handle(route))return;
    if(path==='/api/profile')return route.fulfill({json:{destination:'session_ready',profile:{name:authorization===`Bearer ${tokenB}`?'Bob':'Alice',goal:'Meetings',completed_attempts:0},provider:'openai'}});
    if(path==='/api/history')return route.fulfill({json:[]});
    if(path==='/api/exercises/current')return route.fulfill({json:training&&authorization!==`Bearer ${tokenB}`?{id:'clip',audio_url:'/api/exercises/clip/audio',completed_attempt_id:null}:null});
    if(path==='/api/exercises')return route.fulfill({json:{id:'clip',audio_url:'/api/exercises/clip/audio',completed_attempt_id:null}});
    if(path.endsWith('/conversation/listened'))listened=true;
    if(path.endsWith('/conversation')||path.endsWith('/conversation/listened'))return route.fulfill({json:{state:listened?'AWAITING_SUMMARY':'LISTENING',cue_id:listened?'summary':null,prompt:listened?'Tell me what you understood.':null,active_followup:null,pending_attempt:null,result:null}});
    if(path.endsWith('/coach-audio'))return route.fulfill({contentType:'audio/wav',body:promptWav()});
    return route.fulfill({contentType:'audio/wav',body:promptWav()});
  });
  await page.goto('/');await expect(page.getByRole('button',{name:'I already have an account'})).toBeVisible();await page.getByRole('button',{name:'I already have an account'}).click();
  return {requests,expire(){reject=true;}};
}
async function showSignIn(page:Page){await expect(page.getByRole('button',{name:'Sign in',exact:true}).or(page.getByRole('button',{name:'I already have an account'}))).toBeVisible();if(await page.getByRole('button',{name:'I already have an account'}).count())await page.getByRole('button',{name:'I already have an account'}).click();}
async function signIn(page:Page,email=user.email){await page.getByLabel('Email',{exact:true}).fill(email);await page.getByLabel('Password',{exact:true}).fill('test-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();}
async function signOut(page:Page){await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('button',{name:'Sign out',exact:true}).click();await showSignIn(page);}
async function externalLogout(page:Page){
  // Actual SDK cross-tab lifecycle; no test access to application internals.
  await page.evaluate(()=>{localStorage.removeItem('audli-auth-session');const channel=new BroadcastChannel('audli-auth-session');channel.postMessage({event:'SIGNED_OUT',session:null});channel.close();});
  await showSignIn(page);
}
async function switchToB(page:Page){await externalLogout(page);await signIn(page,userB.email);await expect(page.getByRole('heading',{name:'Good to see you, Bob.'})).toBeVisible();}
async function speaking(page:Page){await expect(page.locator('.session-shell .audli-mascot')).toHaveAttribute('data-state','Speaking');}
async function listening(page:Page){await expect(page.locator('.session-shell .audli-mascot')).toHaveAttribute('data-state','Listening');}

test('unauthenticated Landing is private; sign-in reload and logout preserve ownership',async({page})=>{
  const fixture=await setupAuth(page);expect(fixture.requests).toEqual([]);await signIn(page);await expect(page.getByRole('heading',{name:'Good to see you, Alice.'})).toBeVisible();
  expect(fixture.requests.every(x=>x.authorization===`Bearer ${token}`)).toBe(true);await page.reload();await expect(page.getByRole('heading',{name:'Good to see you, Alice.'})).toBeVisible();await signOut(page);await expect(page.getByText('Alice',{exact:false})).toHaveCount(0);
  await signIn(page);await expect(page.getByRole('heading',{name:'Good to see you, Alice.'})).toBeVisible();
});
test('signup requires email confirmation and initializes no learner',async({page})=>{
  const fixture=await setupAuth(page);await page.getByRole('button',{name:'New to Audli? Create an account'}).click();await page.getByLabel('Email').fill(user.email);await page.getByLabel('Password').fill('test-password');await page.getByRole('button',{name:'Sign up',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Check your email');expect(fixture.requests).toEqual([]);
});
test('invalid persisted session fails closed and requires sign-in',async({page})=>{
  const fixture=await setupAuth(page);await signIn(page);await expect(page.getByRole('heading',{name:'Good to see you, Alice.'})).toBeVisible();fixture.expire();await page.reload();await expect(page.getByRole('status')).toContainText('session expired');await expect(page.getByRole('heading',{name:'Good to see you, Alice.'})).toHaveCount(0);
});
test('auth connection failure exposes only recovery',async({page})=>{
  await page.route('**/api/auth/config',route=>route.fulfill({status:502,contentType:'text/html',body:'Unavailable'}));await page.goto('/');await expect(page.getByRole('button',{name:'Retry connection'})).toBeVisible();await expect(page.getByRole('button',{name:'Start today’s session'})).toHaveCount(0);
});
test('failed remote logout still clears browser credentials',async({page})=>{
  await setupAuth(page);await signIn(page);await expect(page.getByRole('heading',{name:'Good to see you, Alice.'})).toBeVisible();await page.route('https://audli-auth-test.supabase.co/auth/v1/logout**',route=>route.fulfill({status:500,json:{message:'Offline'}}));await signOut(page);await expect(page.getByRole('status')).toContainText('Signed out here');await page.reload();await expect(page.getByRole('button',{name:'I already have an account'})).toBeVisible();
});
test('token refresh continues the same account-bound session',async({page})=>{
  await installVoice(page);const fixture=await setupAuth(page);await signIn(page);await expect(page.getByRole('button',{name:'Start today’s session'})).toBeEnabled();
  await page.evaluate(()=>{const session=JSON.parse(localStorage.getItem('audli-auth-session')!);session.expires_at=Math.floor(Date.now()/1000)-1;localStorage.setItem('audli-auth-session',JSON.stringify(session));});
  const refreshed=page.waitForRequest(request=>request.url().includes('grant_type=refresh_token'));await page.getByRole('button',{name:'Start today’s session'}).click();await refreshed;await speaking(page);expect(fixture.requests.some(x=>x.path==='/api/lessons/lesson/exercise')).toBe(true);
});
test('cross-tab logout stops active capture and never uploads the discarded answer',async({page})=>{
  await installVoice(page);const fixture=await setupAuth(page,true);await signIn(page);await page.getByRole('button',{name:'Start today’s session'}).click();await speaking(page);await endAudio(page);await expect.poll(async()=>(await probe(page)).plays.length).toBe(2);await endAudio(page);await listening(page);
  const before=(await probe(page)).tracks;await externalLogout(page);expect((await probe(page)).tracks).toBeGreaterThan(before);expect(fixture.requests.some(x=>x.path.endsWith('/attempts'))).toBe(false);expect((await probe(page)).revoked).toEqual((await probe(page)).urls);
});
for(const delayed of ['preflight','recording','generation'] as const){
  test(`account switch discards delayed lesson ${delayed}`,async({page})=>{
    await installVoice(page,{delayMedia:delayed==='preflight'?1:delayed==='recording'?2:undefined});const fixture=await setupAuth(page,delayed==='recording');let release:(()=>Promise<void>)|undefined;
    if(delayed==='generation')await page.route('**/api/lessons/lesson/exercise',async route=>{fixture.requests.push({path:'/api/lessons/lesson/exercise',authorization:route.request().headers()['authorization'],method:'POST',body:null});await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({json:{id:'private-A',audio_url:'/api/exercises/private-A/audio',completed_attempt_id:null}}).catch(()=>{});resolve();};});});
    await signIn(page);await page.getByRole('button',{name:'Start today’s session'}).click();
    if(delayed==='recording'){await speaking(page);await endAudio(page);await expect.poll(async()=>(await probe(page)).plays.length).toBe(2);await endAudio(page);}
    if(delayed==='generation')await expect.poll(()=>!!release).toBe(true);else await expect.poll(async()=>page.evaluate(() => typeof (window as unknown as {voiceProbe:{releaseMedia?:()=>void}}).voiceProbe.releaseMedia === 'function')).toBe(true);
    await switchToB(page);const count=fixture.requests.length;
    if(release)await release();else await page.evaluate(()=>(window as unknown as {voiceProbe:{releaseMedia?:()=>void}}).voiceProbe.releaseMedia?.());
    await page.waitForTimeout(150);expect(fixture.requests.length).toBe(count);expect(fixture.requests.filter(x=>x.authorization===`Bearer ${tokenB}`&&x.method!=='GET')).toEqual([]);expect((await probe(page)).recorders).toBe(0);
  });
}

async function setupOnboarding(page:Page,options:Parameters<typeof installVoice>[1]={},legacy=false,withWelcome=false){
  await installVoice(page,options);const fixture=await setupAuth(page);
  const profile={name:'Listener',goal:'Everyday English',target_language:'en',interests:[] as string[],target_situations:[] as string[],onboarding_status:legacy?'profile_saved':'not_started',completed_attempts:0};
  let welcome:'introduction'|'status'|null=null;let accentResponse='Scottish and Australian';
  const accentPreferences={response:'',accents:[] as string[],status:'unspecified'};
  let stage='identity',revision=0,pending:{text:string;confidence:number;uncertainty:string[]}|null=null;
  let failVoice=false,failUpload=false,failComplete=false,uncertain=false,delayAudio=false,releaseAudio:(()=>void)|undefined;
  const revisions:number[]=[];
  let incomplete=0,clarification=false,clarificationCount=0,paused=false;
  const snapshot=()=>({profile:{...profile,accent_preferences:{...accentPreferences}},stage,welcome,revision,pending,clarification_paused:paused,prompt:welcome==='introduction'?"Hey! I’m Audli, your listening buddy. We’ll practice listening together.":welcome==='status'?"My status bubbles say ‘Audli is speaking’, ‘Listening to you’, or ‘Thinking’. You don’t need to watch them.":stage==='accents'?'Are there any English accents you find tricky? You can say skip.':clarification?'What language do you want to train? Audli currently trains English listening.':stage==='review'?'Your listening preferences are saved.':'Tell me about your listening goals.',destination:profile.onboarding_status==='complete'?'session_ready':'onboarding'});
  await page.route('**/api/profile',route=>route.fulfill({json:route.request().headers()['authorization']===`Bearer ${tokenB}`?{destination:'session_ready',profile:{...profile,name:'Bob'},provider:'openai'}:{destination:snapshot().destination,profile,provider:'openai'}}));
  await page.route('**/api/onboarding**',async route=>{
    const path=new URL(route.request().url()).pathname;fixture.requests.push({path,authorization:route.request().headers()['authorization'],method:route.request().method(),body:route.request().postData()});
    if(path.endsWith('/audio')){const requested=route.request().postDataJSON().revision;revisions.push(requested);if(delayAudio){delayAudio=false;await new Promise<void>(resolve=>{releaseAudio=resolve;});}if(requested!==revision)return route.fulfill({status:409,json:{detail:'Onboarding changed'}});if(failVoice)return route.fulfill({status:503,json:{detail:'Voice unavailable'}});return route.fulfill({contentType:'audio/wav',body:promptWav()});}
    if(path.endsWith('/start')&&profile.onboarding_status!=='in_progress'){profile.onboarding_status='in_progress';if(withWelcome&&!legacy)welcome='introduction';revision++;}
    if(path.endsWith('/heard')){welcome=welcome==='introduction'?'status':null;revision++;}
    if(path.endsWith('/retry-clarification')){paused=false;clarificationCount=0;revision++;}
    if(path.endsWith('/attempts')){if(failUpload){failUpload=false;return route.fulfill({status:503,json:{detail:'Transcription failed. Recording is safe.'}});}pending={text:stage==='accents'?accentResponse:'Maya English work meetings and science',confidence:uncertain?.2:.95,uncertainty:uncertain?['unclear']:[]};uncertain=false;revision++;}
    if(path.endsWith('/answer')){const body=route.request().postDataJSON();if(body.revision!==revision)return route.fulfill({status:409,json:{detail:'Stale revision'}});if(incomplete>0){incomplete--;profile.name='Maya';clarification=true;paused=clarificationCount===2;clarificationCount=Math.min(2,clarificationCount+1);pending=null;revision++;return route.fulfill({json:snapshot()});}clarification=false;clarificationCount=0;if(stage==='identity'){profile.name='Maya';stage='needs';}else if(stage==='needs'){profile.goal='Understand meetings';profile.target_situations=['work meetings'];stage='interests';}else if(stage==='accents'){accentPreferences.response=body.text;accentPreferences.accents=body.text==='skip'?[]:['australian','scottish'];accentPreferences.status=body.text==='skip'?'no_preference':'preferred';stage='review';profile.onboarding_status='profile_saved';}else{profile.interests=['science'];stage=withWelcome?'accents':'review';if(!withWelcome)profile.onboarding_status='profile_saved';}pending=null;revision++;}
    if(path.endsWith('/complete')){if(profile.onboarding_status!=='complete'){profile.onboarding_status='complete';revision++;}if(failComplete){failComplete=false;return route.fulfill({status:503,json:{detail:'Response interrupted. Progress is safe.'}});}}
    return route.fulfill({json:snapshot()});
  });
  await signIn(page);await expect(page.getByRole('button',{name:'Let’s talk',exact:true})).toBeVisible();
  return {...fixture,revisions,snapshot,finishWelcomeElsewhere(){welcome=welcome==='introduction'?'status':null;revision++;},accentAnswer(text:string){accentResponse=text;},incompleteAnswers(count=1){incomplete=count;},failSpeech(){failVoice=true;},restoreSpeech(){failVoice=false;},failTranscription(){failUpload=true;},interruptCompletion(){failComplete=true;},uncertainNext(){uncertain=true;},delaySpeech(){delayAudio=true;},get audioPending(){return !!releaseAudio;},advanceRevision(){revision++;},releaseAudio(){releaseAudio?.();}};
}
async function onboardingTurn(page:Page){await speaking(page);await endAudio(page);await listening(page);await page.clock.runFor(2700);await speaking(page);}
async function completeOnboarding(page:Page){await page.getByRole('button',{name:'Let’s talk',exact:true}).click();for(let i=0;i<3;i++)await onboardingTurn(page);await endAudio(page);await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();}

test('incomplete onboarding asks a focused question and records fresh speech after End and refresh',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.incompleteAnswers();await page.clock.install();
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await onboardingTurn(page);
  expect(fixture.snapshot().profile.name).toBe('Maya');expect(fixture.snapshot().pending).toBeNull();
  await expect(page.getByText('What language do you want to train? Audli currently trains English listening.',{exact:true})).toHaveCount(1);
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();await page.reload();
  await expect(page.getByRole('button',{name:'Resume our conversation'})).toBeVisible();
  expect(fixture.requests.filter(x=>x.path.endsWith('/answer'))).toHaveLength(1);
  await page.getByRole('button',{name:'Resume our conversation'}).click();await onboardingTurn(page);
  expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(2);
  expect(fixture.snapshot().stage).toBe('needs');expect((await probe(page)).recorders).toBe(1);
});

test('repeated incomplete onboarding pauses across refresh until explicit retry',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.incompleteAnswers(3);await page.clock.install();
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();
  for(let i=0;i<2;i++)await onboardingTurn(page);
  await endAudio(page);await listening(page);await page.clock.runFor(2700);
  await expect(page.locator('main').getByRole('alert')).toContainText('Let’s pause here');
  expect(fixture.snapshot().clarification_paused).toBe(true);await page.reload();
  await expect(page.getByRole('button',{name:'Resume our conversation'})).toBeVisible();
  expect(fixture.requests.filter(x=>x.path.endsWith('/answer'))).toHaveLength(3);
  expect((await probe(page)).recorders).toBe(0);
  await page.getByRole('button',{name:'Resume our conversation'}).click();await onboardingTurn(page);
  expect(fixture.requests.filter(x=>x.path.endsWith('/retry-clarification'))).toHaveLength(1);
  expect(fixture.snapshot().stage).toBe('needs');
});

test('account switch cancels the fresh clarification recording',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.incompleteAnswers();await page.clock.install();
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await onboardingTurn(page);
  await endAudio(page);await listening(page);await switchToB(page);const count=fixture.requests.length;
  await page.clock.runFor(2700);await page.waitForTimeout(100);
  expect(fixture.requests.length).toBe(count);
  expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(1);
  expect(fixture.requests.filter(x=>x.authorization===`Bearer ${tokenB}`&&x.method!=='GET')).toEqual([]);
});

test('spoken onboarding progresses hands-free, persists profile, and completed learner skips it',async({page})=>{
  const fixture=await setupOnboarding(page);await page.clock.install();await completeOnboarding(page);expect(fixture.requests.filter(x=>x.path==='/api/onboarding/answer')).toHaveLength(3);expect(fixture.snapshot().profile.interests).toEqual(['science']);expect(fixture.requests.some(x=>x.path==='/api/lessons/lesson/exercise')).toBe(false);
  await page.reload();await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();await expect(page.getByRole('button',{name:'Let’s talk',exact:true})).toHaveCount(0);
});
test('pending recognition resumes after refresh without duplicating completed onboarding stages',async({page})=>{
  const fixture=await setupOnboarding(page);await page.clock.install();await page.route('**/api/onboarding/answer',route=>route.fulfill({status:503,json:{detail:'Extraction unavailable'}}));
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);await endAudio(page);await listening(page);await page.clock.runFor(2700);await expect(page.locator('main').getByRole('alert')).toContainText('Extraction unavailable');
  expect(fixture.snapshot().pending?.text).toBeTruthy();await page.unroute('**/api/onboarding/answer');await page.reload();await speaking(page);expect(fixture.snapshot().stage).toBe('needs');expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(1);await expect(page.locator('textarea')).toHaveCount(0);
});
test('logout/login resumes saved onboarding stage without repeating identity',async({page})=>{
  const fixture=await setupOnboarding(page);await page.clock.install();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await onboardingTurn(page);expect(fixture.snapshot().stage).toBe('needs');await externalLogout(page);await signIn(page);await speaking(page);expect(fixture.snapshot().stage).toBe('needs');expect(fixture.requests.filter(x=>x.path.endsWith('/answer'))).toHaveLength(1);
});
test('transcription and interrupted finalization retry preserve completed stages',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.failTranscription();fixture.interruptCompletion();await page.clock.install();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);await endAudio(page);await listening(page);await page.clock.runFor(2700);await expect(page.locator('main').getByRole('alert')).toContainText('Recording is safe');const count=(await probe(page)).recorders;
  await page.getByRole('button',{name:'Retry conversation'}).click();await speaking(page);await endAudio(page);await speaking(page);expect((await probe(page)).recorders).toBe(count);
  for(let i=0;i<2;i++)await onboardingTurn(page);await endAudio(page);await expect(page.locator('main').getByRole('alert')).toContainText('Response interrupted');await page.getByRole('button',{name:'Retry conversation'}).click();await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();expect(fixture.requests.filter(x=>x.path.endsWith('/answer'))).toHaveLength(3);
});
test('onboarding microphone denial and TTS failure are recoverable without corrupting progress',async({page})=>{
  const fixture=await setupOnboarding(page,{denied:true});fixture.failSpeech();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await expect(page.locator('main').getByRole('alert')).toContainText('voice is unavailable');fixture.restoreSpeech();await page.getByRole('button',{name:'Retry conversation'}).click();await speaking(page);await endAudio(page);await expect(page.locator('main').getByRole('alert')).toContainText('Denied');expect(fixture.snapshot().stage).toBe('identity');expect(fixture.snapshot().pending).toBeNull();
});
test('legacy profile_saved learner starts the same persisted spoken flow',async({page})=>{const fixture=await setupOnboarding(page,{},true);await page.clock.install();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await onboardingTurn(page);expect(fixture.snapshot().stage).toBe('needs');});

test('successful real onboarding TTS survives ordinary rerenders and cleans up on End',async({page})=>{
  const fixture=await setupOnboarding(page,{realPlayback:true});await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);const before=await probe(page);
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'Stay here'}).click();expect((await probe(page)).pauses).toEqual(before.pauses);expect((await probe(page)).plays).toEqual(before.plays);expect(fixture.revisions).toEqual([1]);
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();expect((await probe(page)).revoked).toEqual((await probe(page)).urls);expect((await probe(page)).pauses).toContain(before.plays[0]);
});
test('stale onboarding audio revision recovers once with authoritative checkpoint and never replays on rerender',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.delaySpeech();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await expect.poll(()=>fixture.audioPending).toBe(true);expect(fixture.snapshot().revision).toBe(1);fixture.advanceRevision();fixture.releaseAudio();await speaking(page);expect(fixture.revisions).toEqual([1,2]);
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'Stay here'}).click();expect(fixture.revisions).toEqual([1,2]);expect((await probe(page)).plays).toHaveLength(1);
});
test('account switch during successful onboarding audio body discards body without creating A blob or using B credentials',async({page})=>{
  const fixture=await setupOnboarding(page,{delayBody:'/api/onboarding/audio'});await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);await switchToB(page);const count=fixture.requests.length;
  await page.evaluate(()=>(window as unknown as {voiceProbe:{releaseBody?:()=>void}}).voiceProbe.releaseBody?.());await page.waitForTimeout(150);expect(fixture.requests.length).toBe(count);expect((await probe(page)).plays).toEqual([]);expect((await probe(page)).urls).toEqual([]);expect(fixture.requests.filter(x=>x.authorization===`Bearer ${tokenB}`&&x.method!=='GET')).toEqual([]);
});
test('account switch cancels a stale-audio checkpoint reload before it can retry with B credentials',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.delaySpeech();let recovering=false,release:(()=>Promise<void>)|undefined;
  await page.route('**/api/onboarding',async route=>{if(!recovering)return route.fallback();fixture.requests.push({path:'/api/onboarding',authorization:route.request().headers()['authorization'],method:'GET',body:null});await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({json:fixture.snapshot()}).catch(()=>{});resolve();};});});
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await expect.poll(()=>fixture.audioPending).toBe(true);fixture.advanceRevision();recovering=true;fixture.releaseAudio();await expect.poll(()=>!!release).toBe(true);
  await switchToB(page);const count=fixture.requests.length;await release!();await page.waitForTimeout(150);expect(fixture.requests.length).toBe(count);expect(fixture.revisions).toEqual([1]);expect((await probe(page)).urls).toEqual([]);
});
test('onboarding uncertain recognition automatically retries without a recognition form',async({page})=>{
  const fixture=await setupOnboarding(page);fixture.uncertainNext();await page.clock.install();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await onboardingTurn(page);
  expect(fixture.snapshot().stage).toBe('identity');expect(fixture.requests.filter(x=>x.path.endsWith('/answer'))).toHaveLength(0);await expect(page.locator('textarea')).toHaveCount(0);
  await endAudio(page);await listening(page);await page.clock.runFor(2700);await speaking(page);expect(fixture.snapshot().stage).toBe('needs');
});
test('account switch discards delayed successful exercise audio before playback',async({page})=>{
  await installVoice(page,{delayBody:'/api/exercises/clip/audio'});const fixture=await setupAuth(page,true);await signIn(page);await page.getByRole('button',{name:'Start today’s session'}).click();await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);
  await switchToB(page);const count=fixture.requests.length;await page.evaluate(()=>(window as unknown as {voiceProbe:{releaseBody?:()=>void}}).voiceProbe.releaseBody?.());await page.waitForTimeout(150);
  expect(fixture.requests.length).toBe(count);expect((await probe(page)).plays).toEqual([]);expect((await probe(page)).urls).toEqual([]);
});
for(const delayed of ['microphone','transcription','answer','completion','first generation'] as const){
  test(`account switch cancels delayed onboarding ${delayed}`,async({page})=>{
    const fixture=await setupOnboarding(page,{delayMedia:delayed==='microphone'?1:undefined});await page.clock.install();let release:(()=>Promise<void>)|undefined;
    const path=delayed==='transcription'?'/api/onboarding/attempts':delayed==='answer'?'/api/onboarding/answer':delayed==='completion'?'/api/onboarding/complete':'/api/lessons/lesson/exercise';
    if(delayed!=='microphone')await page.route('**'+path,async route=>{fixture.requests.push({path,authorization:route.request().headers()['authorization'],method:'POST',body:route.request().postData()});await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({json:{stage:'needs',revision:99,destination:'session_ready',profile:{name:'A private result'},id:'A-private',audio_url:'/api/exercises/A-private/audio'}}).catch(()=>{});resolve();};});});
    await page.getByRole('button',{name:'Let’s talk',exact:true}).click();
    if(delayed==='first generation'){for(let i=0;i<3;i++)await onboardingTurn(page);await endAudio(page);await expect(page.getByRole('button',{name:'Start today’s session'})).toBeEnabled();await page.getByRole('button',{name:'Start today’s session'}).click();}
    else if(delayed==='completion'){for(let i=0;i<3;i++)await onboardingTurn(page);await endAudio(page);}
    else{await speaking(page);await endAudio(page);if(delayed!=='microphone'){await listening(page);await page.clock.runFor(2700);}}
    if(delayed==='microphone')await expect.poll(async()=>page.evaluate(() => typeof (window as unknown as {voiceProbe:{releaseMedia?:()=>void}}).voiceProbe.releaseMedia === 'function')).toBe(true);else await expect.poll(()=>!!release).toBe(true);
    await switchToB(page);const count=fixture.requests.length;if(release)await release();else await page.evaluate(()=>(window as unknown as {voiceProbe:{releaseMedia?:()=>void}}).voiceProbe.releaseMedia?.());
    await page.waitForTimeout(150);expect(fixture.requests.length).toBe(count);expect(fixture.requests.filter(x=>x.authorization===`Bearer ${tokenB}`&&x.method!=='GET')).toEqual([]);await expect(page.getByText('A private result')).toHaveCount(0);
  });
}

test('Landing and authentication share the polished mobile and desktop visual system without learner requests',async({page})=>{
  const fixture=await setupAuth(page);
  for(const viewport of [{width:320,height:740},{width:1440,height:1000}]) {
    await page.setViewportSize(viewport);await page.reload();
    await expect(page.getByRole('heading',{name:'Train your ears.'})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(viewport.width);
    await expect(page.locator('.wordmark')).toHaveCSS('font-size','24px');
    await page.screenshot({path:`/tmp/aud18-landing-${viewport.width}.png`,fullPage:true});
    await page.getByRole('button',{name:'I already have an account'}).click();
    await expect(page.getByRole('heading',{name:'Welcome back.'})).toBeVisible();
    await expect(page.getByLabel('Email',{exact:true})).toHaveCSS('min-height','58px');
    await expect(page.locator('.audli-mascot')).toHaveCSS('width','120px');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.screenshot({path:`/tmp/aud18-auth-${viewport.width}.png`,fullPage:true});
  }
  expect(fixture.requests).toEqual([]);
});

test('explicit onboarding End stays silent after refresh and relogin while retaining the checkpoint',async({page})=>{
  const fixture=await setupOnboarding(page);await page.clock.install();await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await onboardingTurn(page);
  expect(fixture.snapshot().stage).toBe('needs');const before=fixture.snapshot();
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
  await page.reload();await expect(page.getByRole('button',{name:'Resume our conversation',exact:true})).toBeVisible();
  expect((await probe(page)).plays).toHaveLength(0);expect(fixture.snapshot()).toEqual(before);
  await externalLogout(page);await signIn(page);await expect(page.getByRole('button',{name:'Resume our conversation',exact:true})).toBeVisible();expect((await probe(page)).plays).toHaveLength(0);
  await page.getByRole('button',{name:'Resume our conversation',exact:true}).click();await speaking(page);expect(fixture.snapshot().stage).toBe('needs');
  expect(fixture.requests.filter(x=>x.path==='/api/onboarding/answer')).toHaveLength(1);
});

test('account switch discards a successful inline coaching audio body before any blob or B continuation',async({page})=>{
  await installVoice(page,{delayBody:'/coach-audio'});const fixture=await setupAuth(page,true);await signIn(page);
  await page.getByRole('button',{name:'Start today’s session'}).click();await speaking(page);await endAudio(page);
  await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);const before=await probe(page);
  await switchToB(page);const requests=fixture.requests.length;
  await page.evaluate(()=> (window as unknown as {voiceProbe:Probe}).voiceProbe.releaseBody?.());await page.waitForTimeout(100);
  const after=await probe(page);expect(after.urls).toEqual(before.urls);expect(after.revoked).toEqual(after.urls);expect(after.plays).toEqual(before.plays);expect(fixture.requests.length).toBe(requests);
  await expect(page.getByRole('heading',{name:'Good to see you, Bob.'})).toBeVisible();await expect(page.locator('.session-shell')).toHaveCount(0);
});

test('account switch discards delayed welcome audio without personal recording or later-account continuation',async({page})=>{
  await installVoice(page,{delayBody:'/lessons/greeting/audio'});const fixture=await setupAuth(page);await signIn(page);
  await page.route('**/api/lessons/start',route=>route.fulfill({json:{id:'greeting',revision:0,status:'active',phase:'WELCOME',prompt:'Hi, Alice. How was your day?',pending:null,exercise:null,elapsed_seconds:0,remaining_seconds:600,focus:'details',exercises_completed:0,strength:null,encouragement:'Thanks for listening, Alice.'}}));
  await page.route('**/api/lessons/greeting/audio',route=>route.fulfill({contentType:'audio/wav',body:promptWav()}));
  await page.getByRole('button',{name:'Start today’s session'}).click();await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);
  const before=await probe(page);await switchToB(page);const count=fixture.requests.length;
  await page.evaluate(()=>(window as unknown as {voiceProbe:Probe}).voiceProbe.releaseBody?.());await page.waitForTimeout(150);
  const after=await probe(page);expect(after.plays).toEqual(before.plays);expect(after.urls).toEqual(before.urls);expect(after.recorders).toBe(0);
  expect(fixture.requests.length).toBe(count);expect(fixture.requests.filter(x=>x.authorization===`Bearer ${tokenB}`&&x.method!=='GET')).toEqual([]);
});


async function finishWelcome(page:Page){
  await speaking(page);await endAudio(page);await speaking(page);
  await expect(page.locator('.session-secondary')).toContainText('My status bubbles');
  await endAudio(page);await speaking(page);
}

test('new onboarding speaks welcome and actual status labels before recording, then saves optional accents',async({page})=>{
  const fixture=await setupOnboarding(page,{},false,true);await page.clock.install();
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);
  await expect(page.locator('.session-secondary')).toContainText('listening buddy');
  await expect(page.getByRole('status')).toHaveText('Audli is speaking');
  expect((await probe(page)).recorders).toBe(0);
  await endAudio(page);await speaking(page);
  await expect(page.locator('.session-secondary')).toContainText('Listening to you');
  await expect(page.getByRole('status')).toHaveText('Audli is speaking');
  expect((await probe(page)).recorders).toBe(0);
  await endAudio(page);await speaking(page);expect(fixture.snapshot().welcome).toBeNull();
  for(let i=0;i<4;i++)await onboardingTurn(page);
  expect(fixture.snapshot().profile.accent_preferences.response).toBe('Scottish and Australian');
  await endAudio(page);await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();
  expect(fixture.requests.filter(x=>x.path.endsWith('/heard'))).toHaveLength(2);
  const before=fixture.requests.length;await page.reload();
  await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();
  expect(fixture.requests.slice(before).some(x=>x.path.startsWith('/api/onboarding'))).toBe(false);
});

for(const step of ['introduction','status'] as const){
  test(`welcome ${step} interruption resumes unfinished speech and never opens microphone`,async({page})=>{
    const fixture=await setupOnboarding(page,{},false,true);await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);
    if(step==='status'){await endAudio(page);await speaking(page);}
    expect(fixture.snapshot().welcome).toBe(step);expect((await probe(page)).recorders).toBe(0);
    await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
    await page.reload();await expect(page.getByRole('button',{name:'Resume our conversation'})).toBeVisible();
    expect((await probe(page)).plays).toHaveLength(0);
    await page.getByRole('button',{name:'Resume our conversation'}).click();await speaking(page);
    expect(fixture.snapshot().welcome).toBe(step);expect((await probe(page)).recorders).toBe(0);
    await endAudio(page);await speaking(page);
    expect(fixture.snapshot().welcome).toBe(step==='introduction'?'status':null);
  });
}

test('completed welcome stays completed on refresh during identity and optional accent skip is saved',async({page})=>{
  const fixture=await setupOnboarding(page,{},false,true);fixture.accentAnswer('skip');await page.clock.install();
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await finishWelcome(page);
  await page.reload();await speaking(page);
  expect(fixture.snapshot().welcome).toBeNull();
  await expect(page.locator('.session-secondary')).not.toContainText('listening buddy');
  for(let i=0;i<4;i++)await onboardingTurn(page);
  expect(fixture.snapshot().profile.accent_preferences.status).toBe('no_preference');
  expect(fixture.snapshot().profile.accent_preferences.accents).toEqual([]);
  await endAudio(page);await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();
});

test('account switch cancels welcome without saving heard checkpoint or starting microphone',async({page})=>{
  const fixture=await setupOnboarding(page,{},false,true);await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);
  await switchToB(page);const count=fixture.requests.length;
  await endAudio(page);await page.waitForTimeout(100);
  expect(fixture.requests.length).toBe(count);expect(fixture.snapshot().welcome).toBe('introduction');
  expect((await probe(page)).recorders).toBe(0);
  expect(fixture.requests.filter(x=>x.path.endsWith('/heard'))).toHaveLength(0);
});


test('superseded welcome audio body is discarded before speech or microphone capture',async({page})=>{
  const fixture=await setupOnboarding(page,{delayBody:'/api/onboarding/audio'},false,true);
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();
  await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);
  fixture.finishWelcomeElsewhere();
  await page.evaluate(()=>(window as unknown as {voiceProbe:Probe}).voiceProbe.releaseBody?.());
  await expect.poll(()=>fixture.revisions.length).toBe(2);
  expect((await probe(page)).plays).toHaveLength(0);expect((await probe(page)).recorders).toBe(0);
  expect(fixture.snapshot().welcome).toBe('status');
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
  await page.evaluate(()=>(window as unknown as {voiceProbe:Probe}).voiceProbe.releaseBody?.());
  expect(fixture.requests.filter(x=>x.path.endsWith('/heard'))).toHaveLength(0);
});

test('a welcome completed elsewhere during playback cannot duplicate acknowledgment or start stale capture',async({page})=>{
  const fixture=await setupOnboarding(page,{},false,true);
  await page.getByRole('button',{name:'Let’s talk',exact:true}).click();await speaking(page);
  fixture.finishWelcomeElsewhere();await endAudio(page);await speaking(page);
  await expect(page.locator('.session-secondary')).toContainText('My status bubbles');
  expect(fixture.requests.filter(x=>x.path.endsWith('/heard'))).toHaveLength(0);
  expect((await probe(page)).recorders).toBe(0);
  await endAudio(page);await speaking(page);
  expect(fixture.requests.filter(x=>x.path.endsWith('/heard'))).toHaveLength(1);
  expect(fixture.snapshot().welcome).toBeNull();
});
