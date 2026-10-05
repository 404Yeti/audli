import assert from 'node:assert/strict';
import { test } from 'node:test';
import { captureRecording, recordingPreview, previewSource, recordingUpload, recordingDiagnostic } from '../web/lib/recording.ts';

function setup({ mimeType = 'audio/webm;codecs=opus', supported = 'audio/webm;codecs=opus', failStart = false } = {}) {
  let stoppedTracks = 0;
  let current;
  const input = { getTracks: () => [{ stop() { stoppedTracks++; } }] };
  class Recorder {
    static isTypeSupported(type) { return type === supported; }
    constructor(stream, options) {
      assert.equal(stream, input);
      this.mimeType = mimeType;
      this.options = options;
      this.state = 'inactive';
      current = this;
    }
    start(timeslice) {
      assert.equal(timeslice, 250);
      if (failStart) throw new Error('start failed');
      this.state = 'recording';
    }
    stop() { this.state = 'inactive'; this.stopRequests = (this.stopRequests || 0) + 1; }
    chunk(data) { this.ondataavailable?.({ data }); }
    finish() { this.onstop?.(); }
    error() { this.onerror?.({ error: new Error('do not log payload') }); }
  }
  let completed = null;
  const errors = [];
  const session = captureRecording(input, blob => { completed = blob; }, error => errors.push(error), Recorder);
  return { session, active: current, errors, get blob() { return completed; }, get stoppedTracks() { return stoppedTracks; } };
}

test('final dataavailable is collected before stop; upload uses the complete original Blob', async () => {
  const captured = setup();
  captured.active.chunk(new Blob(['first'], { type: 'audio/webm' }));
  captured.active.chunk(new Blob([]));
  captured.session.stop();
  assert.equal(captured.blob, null);
  captured.active.chunk(new Blob(['final'], { type: 'audio/webm' }));
  captured.active.finish();
  assert.equal(await captured.blob.text(), 'firstfinal');
  assert.equal(captured.blob.type, 'audio/webm;codecs=opus');
  assert.ok(captured.stoppedTracks);
  const file = recordingUpload(captured.blob).get('audio');
  assert.equal(file.name, 'response.webm');
  assert.equal(file.type, captured.blob.type);
  assert.equal(await file.text(), 'firstfinal');
});

test('preview is absent until its URL matches the current Blob; cleanup revokes only its own URL', () => {
  const first = new Blob(['first'], { type: 'audio/webm' });
  const second = new Blob(['second'], { type: 'audio/webm' });
  const revoked = [];
  let next = 0;
  const urls = { createObjectURL: () => `blob:test-${++next}`, revokeObjectURL: url => revoked.push(url) };
  assert.equal(previewSource(null, first), undefined);
  const old = recordingPreview(first, urls);
  assert.equal(previewSource(old, first), 'blob:test-1');
  assert.equal(previewSource(old, second), undefined);
  assert.equal(previewSource(old, null), undefined);
  const replacement = recordingPreview(second, urls);
  old.dispose(); old.dispose();
  assert.deepEqual(revoked, ['blob:test-1']);
  assert.equal(previewSource(replacement, second), 'blob:test-2');
  replacement.dispose();
  assert.deepEqual(revoked, ['blob:test-1', 'blob:test-2']);
});

test('preview cleanup never deletes Blob contents needed by an in-flight upload', async () => {
  const blob = new Blob(['pending upload'], { type: 'audio/wav' });
  const form = recordingUpload(blob);
  const preview = recordingPreview(blob, { createObjectURL: () => 'blob:preview', revokeObjectURL() {} });
  preview.dispose();
  assert.equal(await form.get('audio').text(), 'pending upload');
  assert.equal(await blob.text(), 'pending upload');
});

test('empty recordings are rejected before preview or upload', () => {
  const captured = setup();
  captured.session.stop(); captured.active.finish();
  assert.equal(captured.blob, null);
  assert.equal(captured.errors.length, 1);
  assert.throws(() => recordingUpload(new Blob([])), /empty/);
  assert.throws(() => recordingPreview(new Blob([])), /empty/);
});

test('Safari MP4 MIME and extension are preserved', () => {
  const captured = setup({ mimeType: 'audio/mp4', supported: 'audio/mp4' });
  assert.equal(captured.active.options.mimeType, 'audio/mp4');
  captured.active.chunk(new Blob(['mp4 bytes'], { type: 'audio/mp4' }));
  captured.session.stop(); captured.active.finish();
  assert.equal(recordingUpload(captured.blob).get('audio').name, 'response.mp4');
});

test('chunk MIME is used when recorder MIME is empty', () => {
  const captured = setup({ mimeType: '', supported: '' });
  captured.active.chunk(new Blob(['bytes'], { type: 'audio/ogg' }));
  captured.session.stop(); captured.active.finish();
  assert.equal(captured.blob.type, 'audio/ogg');
});

test('dispose ignores late chunks and stop callbacks from a stale recorder', () => {
  const old = setup();
  const lateChunk = old.active.ondataavailable;
  const lateStop = old.active.onstop;
  old.session.dispose();
  lateChunk({ data: new Blob(['stale audio']) }); lateStop();
  assert.equal(old.blob, null);
  assert.equal(old.errors.length, 0);
  assert.ok(old.stoppedTracks);
  const next = setup();
  next.active.chunk(new Blob(['new'], { type: 'audio/webm' }));
  next.session.stop(); next.active.finish();
  assert.equal(next.blob.size, 3);
});

test('recorder errors cannot publish a partial successful recording', () => {
  const captured = setup();
  captured.active.chunk(new Blob(['partial'], { type: 'audio/webm' }));
  captured.active.error(); captured.active.finish();
  assert.equal(captured.blob, null);
  assert.equal(captured.errors.length, 1);
});

test('unsupported MIME and oversized audio are rejected without upload', () => {
  assert.throws(() => recordingUpload(new Blob(['data'], { type: 'text/plain' })), /unsupported/);
  assert.throws(() => recordingUpload(new Blob([new Uint8Array(12*1024*1024+1)], { type: 'audio/wav' })), /12 MB/);
});

test('duplicate stop is safe; stopping does not prematurely publish a Blob', () => {
  const captured = setup();
  captured.session.stop(); captured.session.stop();
  assert.equal(captured.active.stopRequests, 1);
  assert.equal(captured.blob, null);
});

test('production emits no recording diagnostics', () => {
  const previousMode = process.env.NODE_ENV;
  const previousDebug = console.debug;
  const calls = [];
  process.env.NODE_ENV = 'production'; console.debug = (...args) => calls.push(args);
  try { recordingDiagnostic('recorder_started', { blobSize: 15, mimeType: 'audio/webm' }); assert.equal(calls.length, 0); }
  finally { process.env.NODE_ENV = previousMode; console.debug = previousDebug; }
});
