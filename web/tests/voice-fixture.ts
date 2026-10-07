import type { Page } from '@playwright/test';
export type Probe = { plays: string[]; pauses: string[]; urls: string[]; revoked: string[]; tracks: number; recorders: number; mediaCalls: number; releaseMedia?: () => void; bodyPending: boolean; releaseBody?: () => void; clicks: number };
export function promptWav() {
  const wav = Buffer.alloc(44 + 8000 * 5 * 2);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ',8);
  wav.writeUInt32LE(16,16); wav.writeUInt16LE(1,20); wav.writeUInt16LE(1,22);
  wav.writeUInt32LE(8000,24); wav.writeUInt32LE(16000,28); wav.writeUInt16LE(2,32); wav.writeUInt16LE(16,34);
  wav.write('data',36); wav.writeUInt32LE(wav.length - 44,40); return wav;
}
export async function installVoice(page: Page, options: { denied?: boolean; delayMedia?: number; delayBody?: string; realPlayback?: boolean; autoPlayback?: boolean; continuousVoice?: boolean } = {}) {
  await page.addInitScript(options => {
    const probe: Probe = {plays:[],pauses:[],urls:[],revoked:[],tracks:0,recorders:0,mediaCalls:0,bodyPending:false,clicks:0};
    (window as unknown as { voiceProbe: Probe }).voiceProbe = probe;
    document.addEventListener('click', () => probe.clicks++);
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); probe.urls.push(url); return url; };
    URL.revokeObjectURL = url => { probe.revoked.push(url); revoke(url); };
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async () => {
      probe.mediaCalls++;
      if (options.denied) throw new DOMException('Denied','NotAllowedError');
      let stopped = false;
      const stream = {getTracks:() => [{stop(){if(!stopped){probe.tracks++;stopped=true;}}}]};
      if (probe.mediaCalls === options.delayMedia) await new Promise<void>(resolve => { probe.releaseMedia = resolve; });
      return stream;
    }});
    class Recorder {
      static isTypeSupported(){return true;} state='inactive'; mimeType='audio/webm';
      ondataavailable: ((value:{data:Blob})=>void)|null=null; onstop:(()=>void)|null=null; onerror:(()=>void)|null=null;
      constructor(){probe.recorders++;}
      start(){this.state='recording';}
      stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['spoken answer'],{type:this.mimeType})});this.onstop?.();});}
    }
    Object.defineProperty(window,'MediaRecorder',{value:Recorder});
    class Context {
      started=Date.now();
      createAnalyser(){const started=this.started;return {fftSize:1024,getFloatTimeDomainData(samples:Float32Array){samples.fill(options.continuousVoice || Date.now()-started<800 ? .05 : 0);}};}
      createMediaStreamSource(){return {connect(){},disconnect(){}};}
      async resume(){} async close(){}
    }
    Object.defineProperty(window,'AudioContext',{value:Context});
    const realPlay=HTMLMediaElement.prototype.play, realPause=HTMLMediaElement.prototype.pause;
    HTMLMediaElement.prototype.play=async function(){
      if(options.realPlayback) await realPlay.call(this);
      else {Object.defineProperty(this,'paused',{configurable:true,value:false});this.dispatchEvent(new Event('play'));if(options.autoPlayback)setTimeout(()=>this.dispatchEvent(new Event('ended')),80);}
      probe.plays.push(this.src);
    };
    HTMLMediaElement.prototype.pause=function(){probe.pauses.push(this.src);if(options.realPlayback)realPause.call(this);else Object.defineProperty(this,'paused',{configurable:true,value:true});};
    if(options.delayBody){
      const fetch=window.fetch.bind(window);
      window.fetch=async (...args)=>{const response=await fetch(...args);if(String(args[0]).endsWith(options.delayBody!) && response.ok){const blob=response.blob.bind(response);response.blob=async()=>{const body=await blob();probe.bodyPending=true;await new Promise<void>(resolve=>{probe.releaseBody=resolve;});return body;};}return response;};
    }
  }, options);
}
export async function probe(page: Page) {return page.evaluate(()=> (window as unknown as {voiceProbe:Probe}).voiceProbe);}
export async function endAudio(page: Page) {await page.locator('audio').evaluate(element=>element.dispatchEvent(new Event('ended')));}
