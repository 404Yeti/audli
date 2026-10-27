// Real P2 sender and unchanged server policy; synthetic audio; loopback only.
import { pathToFileURL } from 'node:url';
import { readFile } from 'node:fs/promises';
import process from 'node:process';
const spec = JSON.parse(await readFile(process.argv[2], 'utf8'));
if (!spec.base.startsWith('http://127.0.0.1:')) throw new Error('Loopback required');
const { deliver } = await import(pathToFileURL(`${spec.root}/web/lib/upload-sender.ts`));
const { parseUploadPolicy } = await import(pathToFileURL(`${spec.root}/web/lib/upload-policy.ts`));
const { ApiError } = await import(pathToFileURL(`${spec.root}/web/lib/api.ts`));
const bytes = await readFile(spec.audio);
const rows = await Promise.all(spec.learners.map(async row => {
  let sends = 0, polls = 0, replay = false;
  const headers = { Authorization: `Bearer ${row.token}` };
  const started = performance.now();
  const policy = parseUploadPolicy(await (await fetch(`${spec.base}/api/upload-policy`, { headers })).json());
  if (!policy.enabled) throw new Error('Local retry policy unavailable');
  const attempt = async signal => {
    sends++;
    const body = new FormData(); body.append('audio', new Blob([bytes], {type:'audio/wav'}), 'response.wav');
    let response = await fetch(`${spec.base}/api/exercises/${row.exercise}/attempts`, {
      method:'POST', headers:{...headers,'X-Audli-Upload-Id':row.key}, body, signal });
    // Prototype status protocol only: 202 is not acceptance, and polling sends no audio.
    while (response.status === 202 && spec.mode === 'claims') {
      polls++;
      await new Promise((resolve,reject) => {
        const cancel = () => { clearTimeout(timer); reject(new DOMException('Cancelled','AbortError')); };
        const timer = setTimeout(() => { signal.removeEventListener('abort',cancel); resolve(); },250);
        signal.addEventListener('abort',cancel,{once:true});
        if(signal.aborted) cancel();
      });
      response = await fetch(`${spec.base}/api/poc/uploads/${row.exercise}/${row.key}`, {headers,signal});
    }
    if (!response.ok) throw new ApiError('Synthetic request failure',response.status);
    replay ||= response.headers.get('x-audli-replayed') === 'true';
    const result = await response.json();
    if (!result.id || !result.transcription) throw new Error('Not a completed upload');
    return result;
  };
  try {
    await deliver({ policy:{...policy,deadlineMs:policy.deadlineMs-(performance.now()-started)},bytes:bytes.length,attempt,
      canRetry:async signal => parseUploadPolicy(await (await fetch(`${spec.base}/api/upload-policy`,{headers,signal})).json()).enabled });
    return {ok:true,ms:performance.now()-started,sends,polls,replay};
  } catch { return {ok:false,ms:performance.now()-started,sends,polls,replay}; }
}));
console.log(JSON.stringify(rows));
