import { test, expect, type Page } from '@playwright/test';
import { installVoice, promptWav, probe } from './voice-fixture';
type Voice = 'original' | 'male';
const user={id:'00000000-0000-4000-8000-000000000046',email:'voice@example.com',email_confirmed_at:'2026-10-06T00:00:00Z',app_metadata:{provider:'email',providers:['email']},user_metadata:{},aud:'authenticated',created_at:'2026-10-06T00:00:00Z'};
const userB={...user,id:'00000000-0000-4000-8000-000000000047',email:'other@example.com'};
const tokenFor=(id:string)=>[Buffer.from('{"alg":"HS256"}').toString('base64url'),Buffer.from(JSON.stringify({sub:id,exp:Math.floor(Date.now()/1000)+3600})).toString('base64url'),'fixture'].join('.');
const token=tokenFor(user.id),tokenB=tokenFor(userB.id);
async function login(page:Page,email=user.email){
  const landing=page.getByRole('button',{name:'I already have an account'});await expect(landing.or(page.getByRole('button',{name:'Sign in',exact:true}))).toBeVisible();if(await landing.count())await landing.click();
  await page.getByLabel('Email',{exact:true}).fill(email);await page.getByLabel('Password',{exact:true}).fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByRole('button',{name:'Settings',exact:true}).click();await expect(page.getByRole('button',{name:'Save tutor voice'})).toBeVisible();
}
async function setup(page:Page,options:{failedSave?:boolean;failedPreview?:boolean;unavailable?:boolean}={}){
  await installVoice(page);const voices=new Map<string,Voice>();const writes:Voice[]=[];const previews:string[]=[];let fail=options.failedSave;
  await page.route('https://audli-auth-test.supabase.co/**',route=>{
    const path=new URL(route.request().url()).pathname;if(path.endsWith('/logout'))return route.fulfill({status:204});
    const second=route.request().postData()?.includes(userB.email);return route.fulfill({json:{access_token:second?tokenB:token,refresh_token:'fixture',expires_in:3600,token_type:'bearer',user:second?userB:user}});
  });
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname,auth=route.request().headers().authorization??'';
    if(path==='/api/auth/config')return route.fulfill({json:{mode:'supabase'}});
    if(![`Bearer ${token}`,`Bearer ${tokenB}`].includes(auth))return route.fulfill({status:401,json:{detail:'Sign in to continue.'}});
    if(path==='/api/profile/tutor-voice'){
      if(route.request().method()==='PUT'){const voice=route.request().postDataJSON().voice as Voice;writes.push(voice);if(fail){fail=false;return route.fulfill({status:503,json:{detail:'Please retry saving your voice.'}});}voices.set(auth,voice);}
      return route.fulfill({json:{voice:voices.get(auth)??'original',available:!options.unavailable}});
    }
    if(path.startsWith('/api/profile/tutor-voice/preview/')){previews.push(path);return options.failedPreview?route.fulfill({status:503,json:{detail:'Preview unavailable'}}):route.fulfill({contentType:'audio/wav',body:promptWav()});}
    if(path==='/api/profile')return route.fulfill({json:{destination:'session_ready',profile:{name:auth===`Bearer ${tokenB}`?'Other':'Maya',goal:'Meetings',completed_attempts:2},provider:'openai'}});
    if(path==='/api/onboarding/reset-controls')return route.fulfill({json:{restart_available:true,onboarding_revision:4,reset_description:'Preferences reset; progress stays saved.'}});
    if(path==='/api/history')return route.fulfill({json:[]});
    if(path==='/api/exercises/current'||path==='/api/lessons/current')return route.fulfill({json:null});
    return route.fulfill({status:404,json:{detail:'Unexpected request'}});
  });
  await page.goto('/');await login(page);await expect(page.getByRole('radio',{name:/Audli — Original/})).toBeChecked();return{voices,writes,previews};
}
for(const width of [320,390,1440])test(`voice Settings defaults persistence screenshots ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});const fixture=await setup(page);
  await page.screenshot({path:`../docs/screenshots/AUD-46-settings-original-${width}.png`,fullPage:true,animations:'disabled',style:'nextjs-portal {display:none}'});
  await page.getByRole('radio',{name:'Audli — Male',exact:true}).check();await page.getByRole('button',{name:'Save tutor voice'}).click();await expect(page.getByRole('status')).toContainText('Tutor voice saved');
  await page.screenshot({path:`../docs/screenshots/AUD-46-settings-male-${width}.png`,fullPage:true,animations:'disabled',style:'nextjs-portal {display:none}'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.reload();await page.getByRole('button',{name:'Settings',exact:true}).click();await expect(page.getByRole('radio',{name:'Audli — Male',exact:true})).toBeChecked();
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await login(page);await expect(page.getByRole('radio',{name:'Audli — Male',exact:true})).toBeChecked();
  await page.getByRole('radio',{name:/Audli — Original/}).check();await page.getByRole('button',{name:'Save tutor voice'}).click();await expect(page.getByRole('status')).toContainText('Tutor voice saved');
  expect(fixture.writes).toEqual(['male','original']);expect(fixture.previews).toEqual([]);expect((await probe(page)).mediaCalls).toBe(0);
});
test('voice preview is explicit and saving never interrupts current playback',async({page})=>{
  const fixture=await setup(page);await page.getByRole('button',{name:'Preview Audli — Original'}).click();await expect(page.locator('audio[aria-label="Audli — Original preview"]')).toBeVisible();
  await expect.poll(async()=>(await probe(page)).plays.length).toBe(1);const pauses=(await probe(page)).pauses.length;
  await page.getByRole('radio',{name:'Audli — Male',exact:true}).check();await page.getByRole('button',{name:'Save tutor voice'}).click();await expect(page.getByRole('status')).toContainText('Tutor voice saved');
  expect((await probe(page)).pauses).toHaveLength(pauses);expect((await probe(page)).plays).toHaveLength(1);expect(fixture.previews).toEqual(['/api/profile/tutor-voice/preview/original']);
  await page.getByRole('button',{name:'Home',exact:true}).click();expect((await probe(page)).revoked).toEqual((await probe(page)).urls);
});
test('voice failed save and unavailable preview preserve preference',async({page})=>{
  const fixture=await setup(page,{failedSave:true,failedPreview:true});await page.getByRole('radio',{name:'Audli — Male',exact:true}).check();await page.getByRole('button',{name:'Save tutor voice'}).click();await expect(page.locator('.tutor-voice-settings').getByRole('alert')).toContainText('retry saving');expect(fixture.voices.size).toBe(0);
  await page.getByRole('button',{name:'Save tutor voice'}).click();await expect(page.getByRole('status')).toContainText('Tutor voice saved');await page.getByRole('button',{name:'Preview Audli — Male'}).click();await expect(page.locator('.tutor-voice-settings').getByRole('alert')).toContainText('preview is unavailable');await expect(page.getByRole('radio',{name:'Audli — Male',exact:true})).toBeChecked();
});
test('voice account switching isolates choice and releases preview',async({page})=>{
  await setup(page);await page.getByRole('radio',{name:'Audli — Male',exact:true}).check();await page.getByRole('button',{name:'Save tutor voice'}).click();await expect(page.getByRole('status')).toContainText('Tutor voice saved');await page.getByRole('button',{name:'Preview Audli — Male'}).click();await expect.poll(async()=>(await probe(page)).plays.length).toBe(1);
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await login(page,userB.email);await expect(page.getByRole('radio',{name:/Audli — Original/})).toBeChecked();expect((await probe(page)).revoked).toEqual((await probe(page)).urls);
});
test('voice unavailable provider disables previews',async({page})=>{await setup(page,{unavailable:true});await expect(page.getByText('Voice options are temporarily unavailable. Your preference stays saved.')).toBeVisible();await expect(page.getByRole('button',{name:'Preview Audli — Male'})).toBeDisabled();});
