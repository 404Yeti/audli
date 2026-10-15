import assert from 'node:assert/strict';
import { test } from 'node:test';
import { googleRedirect, googleCallback } from '../web/lib/google-oauth.ts';
test('Google redirects require exact production or local origins',()=>{
  for(const origin of ['https://app.audli.ai','http://127.0.0.1:3000','http://localhost:3000','http://127.0.0.1:3100']) assert.equal(googleRedirect(origin),origin+'/auth/callback');
  for(const origin of ['https://app.audli.ai.evil.test','https://preview.vercel.app','http://app.audli.ai','https://app.audli.ai@evil.test','//evil.test','http://localhost:9999']) assert.throws(()=>googleRedirect(origin));
});
test('callback requires one code and fresh same-tab transaction; ignores next',()=>{
  const url='https://app.audli.ai/auth/callback?code=abc';
  assert.equal(googleCallback(url+'&next=https://evil.test','1000',2000),'abc');
  for(const candidate of [url+'&code=def',url+'#access_token=secret',url.replace('code=abc','error=access_denied'),url.replace('callback','recovery'),url.replace('code=abc','code=')]) assert.throws(()=>googleCallback(candidate,'1000',2000));
  for(const started of [null,'invalid','0','3000']) assert.throws(()=>googleCallback(url,started,700000));
});
