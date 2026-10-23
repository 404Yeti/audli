import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Resampler, floatToPcm16, pcm16ToBase64, rms, createTurnDetector } from '../experiments/realtime-transcription/browser/audio-utils.js';

test('resampler keeps the sample count right across chunk boundaries and is continuous', () => {
  const sine = Float32Array.from({ length: 4800 }, (_, i) => Math.sin(2 * Math.PI * 440 * i / 48000));
  const whole = new Resampler(48000, 24000).process(sine);
  const r = new Resampler(48000, 24000), parts = [];
  for (let i = 0; i < sine.length; i += 128) parts.push(...r.process(sine.slice(i, i + 128)));
  assert.ok(Math.abs(whole.length - 2400) <= 1, `whole ${whole.length}`);
  assert.ok(Math.abs(parts.length - whole.length) <= 1, `chunked ${parts.length} vs ${whole.length}`);
  for (let i = 0; i < Math.min(parts.length, whole.length); i++) assert.ok(Math.abs(parts[i] - whole[i]) < 1e-3, `sample ${i}`);
});

test('44.1 kHz input (iPhone hardware rate) yields 24 kHz output of the right length', () => {
  const out = new Resampler(44100, 24000).process(new Float32Array(44100));
  assert.ok(Math.abs(out.length - 24000) <= 2, String(out.length));
});

test('pcm16 conversion clamps and base64 round-trips', () => {
  const pcm = floatToPcm16(Float32Array.from([0, 1, -1, 2, -2, .5]));
  assert.deepEqual([...pcm], [0, 32767, -32767, 32767, -32768, 16384]);
  const b64 = pcm16ToBase64(pcm);
  assert.deepEqual([...new Int16Array(Uint8Array.from(Buffer.from(b64, 'base64')).buffer)], [...pcm]);
  assert.equal(rms(Float32Array.from([1, -1, 1, -1])), 1);
});

test('turn detector matches production rules: 2 s minimum, 1.8 s window, adaptive learns pauses', () => {
  const run = (config, pattern) => {                           // pattern: [ms, level] segments, 50 ms ticks
    const detect = createTurnDetector(config); let now = 0;
    for (const [ms, level] of pattern) for (let t = 0; t < ms; t += 50) { now += 50; if (detect(now, level).end) return now; }
    return null;
  };
  assert.equal(run({}, [[3000, .1], [5000, 0]]), 3000 + 1800);
  assert.equal(run({ windowMs: 1200 }, [[500, .1], [4000, 0]]), 2000);
  assert.equal(run({ windowMs: 1200 }, [[1000, .1], [1300, 0], [1000, .1], [5000, 0]]), 1000 + 1200);   // cut off by the fixed 1.2 s window
  const adaptiveEnd = run({ windowMs: 1200, adaptive: true }, [[1000, .1], [1100, 0], [1000, .1], [1300, 0], [1000, .1], [5000, 0]]);
  assert.ok(adaptiveEnd > 1000 + 1100 + 1000 + 1300 + 1000, String(adaptiveEnd));
});
