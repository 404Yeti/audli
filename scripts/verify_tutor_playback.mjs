// Local HTTP -> authenticated FastAPI -> provider -> sanitized Blob -> playing.
import { chromium } from '../web/node_modules/playwright/index.mjs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const directory=resolve(process.argv[2]);
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();
const rows=[];
try {
  await page.goto('http://127.0.0.1:8025/__aud25');
  await page.click('button');
  for (const provider of ['elevenlabs','openai']) {
    await page.evaluate(async provider=>{
      await fetch(`/__aud25/provider/${provider}`,{method:'POST'});
    },provider);
    for (const cue of ['onboarding','acknowledgment']) {
      const result=await page.evaluate(async cue=>{
        const headers={'Authorization':'Bearer local-aud25-fixture','Content-Type':'application/json'};
        if (cue==='onboarding') {
          const current=await (await fetch('/api/onboarding',{headers})).json();
          await fetch('/api/onboarding/start',{method:'POST',headers,body:JSON.stringify({revision:current.revision})});
        }
        const revision=(await (await fetch('/api/onboarding',{headers})).json()).revision;
        const path=cue==='onboarding'?'/api/onboarding/audio':'/api/recognition/acknowledgment-audio';
        const body=JSON.stringify(cue==='onboarding'?{revision}:{});
        const started=performance.now();
        const response=await fetch(path,{method:'POST',headers,body});
        if(!response.ok)throw new Error(`Audio HTTP ${response.status}`);
        const blob=await response.blob();
        const responseMs=performance.now()-started;
        const audio=new Audio();const url=URL.createObjectURL(blob);
        let playingMs;
        try {
          await new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>reject(new Error('Playback timeout')),10000);
            audio.onplaying=()=>{clearTimeout(timer);playingMs=performance.now()-started;resolve();};
            audio.onerror=()=>{clearTimeout(timer);reject(new Error('Decode error'));};
            audio.src=url;audio.play().catch(error=>{clearTimeout(timer);reject(error);});
          });
        } finally {audio.pause();audio.removeAttribute('src');URL.revokeObjectURL(url);}
        const cachedStarted=performance.now();
        const cached=await fetch(path,{method:'POST',headers,body});
        await cached.blob();
        if(!cached.ok)throw new Error('Cache read failed');
        return {response_ms:responseMs,playing_ms:playingMs,cache_response_ms:performance.now()-cachedStarted,
          content_type:blob.type,bytes:blob.size,
          tts:JSON.parse(response.headers.get('X-Aud25-Probe-TTS')),
          cached_tts:JSON.parse(cached.headers.get('X-Aud25-Probe-TTS')),
          encoded:await new Promise(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.readAsDataURL(blob);})};
      },cue);
      const {encoded,...metrics}=result;
      await writeFile(`${directory}/${provider}-${cue}.mp3`,Buffer.from(encoded,'base64'));
      rows.push({provider,cue,...metrics});
      await writeFile(`${directory}/playback.json`,JSON.stringify({browser:browser.version(),
        method:'Local authenticated HTTP POST start to Chromium playing; sanitized full Blob; no production proxy; synthetic account; not acoustic onset',rows},null,2));
      console.log(provider,cue,'playing_ms',Math.round(metrics.playing_ms));
    }
  }
  const budget=JSON.parse(await readFile(`${directory}/budget.json`,'utf8'));
  if(budget.attempts>6 || budget.characters>600 || rows.some(r=>r.cached_tts.length))throw new Error('Budget/cache assertion failed');
  console.log('Capped playback test complete:',budget.attempts,'synthesis attempts,',budget.characters,'characters');
} finally {await browser.close();}
