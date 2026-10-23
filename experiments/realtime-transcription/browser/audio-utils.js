// Pure helpers for the Safari/iPhone test page (also unit-tested in Node). No network, no DOM.

/** Streaming linear resampler: feed Float32 chunks at `from` Hz, get chunks at `to` Hz, continuous across calls. */
export class Resampler {
  constructor(from, to) { this.step = from / to; this.pos = 0; this.last = 0; }
  process(input) {
    const out = [];
    let pos = this.pos;
    while (true) {
      const i = Math.floor(pos);
      if (i + 1 >= input.length) break;
      const a = i < 0 ? this.last : input[i];
      out.push(a + (input[i + 1] - a) * (pos - i));
      pos += this.step;
    }
    this.pos = pos - input.length;
    if (input.length) this.last = input[input.length - 1];
    return Float32Array.from(out);
  }
}

export function floatToPcm16(samples) {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) out[i] = Math.max(-32768, Math.min(32767, Math.round(samples[i] * 32767)));
  return out;
}

export function pcm16ToBase64(pcm) {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function rms(samples) {
  if (!samples.length) return 0;
  let sum = 0;
  for (const s of samples) sum += s * s;
  return Math.sqrt(sum / samples.length);
}

/** Same rules as web/lib/hands-free.ts turnDetector, driven by SAMPLE time (ms) so scheduling jitter cannot change it. */
export function createTurnDetector({ windowMs = 1800, adaptive = false, adaptiveCapMs = 2500, minTotalMs = 2000 } = {}) {
  let voiced = 0, lastVoice = 0, previous = 0, firstVoice = null, longestPause = 0, pauseStart = 0, inPause = false;
  return (now, level) => {
    const active = level >= 0.015;
    if (active) {
      if (firstVoice === null) firstVoice = now;
      if (inPause) { longestPause = Math.max(longestPause, now - pauseStart); inPause = false; }
      voiced += Math.min(100, Math.max(0, now - previous));
      lastVoice = now;
    } else if (firstVoice !== null && !inPause) { inPause = true; pauseStart = lastVoice; }
    previous = now;
    const window = adaptive ? Math.min(adaptiveCapMs, Math.max(windowMs, Math.floor(1.25 * longestPause))) : windowMs;
    const end = now >= 119000 || (voiced >= 300 && now >= minTotalMs && now - lastVoice >= window) || (voiced < 300 && now >= 15000);
    return { active, end, lastVoice, firstVoice, window };
  };
}
