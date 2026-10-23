// Chromium smoke test of index.html with a FAKE microphone fed from a synthetic WAV (loops). Not a Safari test.
//   WEB_DIR=<repo>/web node smoke.mjs <synthetic.wav> <token> [turns]    (serve.py must be running on :8765)
import { createRequire } from 'node:module';
const require = createRequire(process.env.WEB_DIR + '/');
const { chromium, webkit } = require('@playwright/test');
const [wav, token, turns = '3'] = process.argv.slice(2);
const run = async (engine, name, fake) => {
  let browser;
  try {
    browser = await engine.launch(fake ? { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${wav}`] } : {});
  } catch (error) { return { engine: name, launched: false, reason: String(error.message).split('\n')[0].slice(0, 160) }; }
  const context = await browser.newContext({ permissions: ['microphone'] });
  const page = await context.newPage();
  await page.goto(`http://127.0.0.1:8765/index.html#${token}`);
  if (!fake) {   // capability probe only: no microphone to feed
    const caps = await page.evaluate(() => ({ audioWorklet: typeof AudioWorkletNode !== 'undefined', webSocket: typeof WebSocket !== 'undefined', getUserMedia: !!navigator.mediaDevices?.getUserMedia }));
    await browser.close(); return { engine: name, launched: true, ...caps };
  }
  await page.click('#start');
  await page.waitForFunction(n => (document.getElementById('log').textContent.match(/^turn \d+:/gm) || []).length >= n, Number(turns), { timeout: 120000 }).catch(() => {});
  const log = await page.textContent('#log'), caps = await page.textContent('#caps');
  await browser.close();
  return { engine: name, launched: true, turnsCompleted: (log.match(/^turn \d+:/gm) || []).length, log: log.replace(/\n\s+".*"/g, ''), caps: JSON.parse(caps) };
};
console.log(JSON.stringify([await run(chromium, 'chromium', true), await run(webkit, 'webkit', false)], null, 1));
