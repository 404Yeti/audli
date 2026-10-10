/** Temporary, development-only metadata diagnostics. Never pass audio or transcripts. */
export function recordingDiagnostic(event: string, metadata: Record<string, string | number | boolean> = {}) {
  if (process.env.NODE_ENV === 'development') console.debug('[Audli recording]', event, metadata);
}

const MIME_EXTENSIONS: Record<string, string> = {
  'audio/webm': 'webm', 'video/webm': 'webm', 'audio/mp4': 'mp4',
  'audio/ogg': 'ogg', 'audio/wav': 'wav', 'audio/x-wav': 'wav',
  'audio/mpeg': 'mp3', 'audio/x-m4a': 'm4a',
};

export function recordingUpload(blob: Blob): FormData {
  if (!blob.size) throw new Error('The recording is empty. Please record again.');
  const extension = MIME_EXTENSIONS[blob.type.split(';')[0].trim().toLowerCase()];
  if (!extension) throw new Error('This recording format is unsupported. Please use WebM, MP4, Ogg, MP3 or WAV audio.');
  if (blob.size > 12 * 1024 * 1024) throw new Error('The recording exceeds 12 MB. Please record a shorter summary.');
  const form = new FormData();
  // Send the original Blob, not its preview URL. Do not set multipart Content-Type manually.
  form.append('audio', blob, `response.${extension}`);
  return form;
}

export function recordingPreview(blob: Blob, urls: Pick<typeof URL, 'createObjectURL' | 'revokeObjectURL'> = URL) {
  if (!blob.size) throw new Error('The recording is empty. Please record again.');
  const url = urls.createObjectURL(blob);
  if (!url) throw new Error('Recording preview could not be created.');
  recordingDiagnostic('object_url_created', { blobSize: blob.size, mimeType: blob.type });
  let released = false;
  return { blob, url, dispose() {
    if (released) return;
    released = true;
    urls.revokeObjectURL(url);
    recordingDiagnostic('object_url_revoked');
  } };
}

export type RecordingPreview = ReturnType<typeof recordingPreview>;
export function previewSource(preview: RecordingPreview | null, blob: Blob | null): string | undefined {
  // A replaced Blob must never temporarily play the previous recording's URL.
  return blob && preview?.blob === blob && preview.url ? preview.url : undefined;
}

export function captureRecording(
  input: MediaStream,
  complete: (blob: Blob) => void,
  failure: (message: string) => void,
  Recorder: typeof MediaRecorder = MediaRecorder,
) {
  let active: MediaRecorder;
  try {
    const mimeType = typeof Recorder.isTypeSupported === 'function'
      ? ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => Recorder.isTypeSupported(type))
      : undefined;
    active = new Recorder(input, mimeType ? { mimeType } : undefined);
  } catch (error) {
    input.getTracks().forEach(track => track.stop());
    throw error;
  }
  let disposed = false;
  let failed = false;
  let started = false;
  let resolveReady!: () => void;
  let rejectReady!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  // Callers may not need readiness; failure must not become an unhandled rejection.
  void ready.catch(() => {});
  const startup = setTimeout(() => {
    if (disposed || started || failed) return;
    failed = true;
    rejectReady(new Error('Microphone recording could not start. Please retry.'));
    if (active.state !== 'inactive') active.stop();
    stopTracks();
    failure('Microphone recording could not start. Please retry.');
  }, 5000);
  const chunks: Blob[] = [];
  const stopTracks = () => input.getTracks().forEach(track => track.stop());
  const detach = () => { clearTimeout(startup); active.onstart = null; active.ondataavailable = null; active.onstop = null; active.onerror = null; chunks.length = 0; };
  active.onstart = () => {
    if (disposed || failed || active.state !== 'recording') return;
    started = true; clearTimeout(startup); resolveReady();
  };
  active.ondataavailable = event => {
    if (disposed || failed || !event.data.size) return;
    chunks.push(event.data);
    recordingDiagnostic('chunk_received', { chunkCount: chunks.length, chunkSize: event.data.size, mimeType: event.data.type });
  };
  active.onstop = () => {
    stopTracks();
    if (!started) rejectReady(new DOMException('Recording stopped before readiness.', 'AbortError'));
    if (disposed) { detach(); return; }
    // The final dataavailable precedes stop. Build only after all chunks have arrived.
    const mimeType = active.mimeType || chunks.find(chunk => chunk.type)?.type || '';
    const blob = new Blob(chunks, { type: mimeType });
    recordingDiagnostic('recorder_stopped', { chunkCount: chunks.length, blobSize: blob.size, mimeType: blob.type });
    detach();
    if (failed) return;
    if (!blob.size || !MIME_EXTENSIONS[blob.type.split(';')[0].trim().toLowerCase()]) {
      failure('The browser did not produce usable audio. Please record again or upload a recording.');
      return;
    }
    complete(blob);
  };
  active.onerror = () => {
    if (disposed || failed) return;
    failed = true;
    clearTimeout(startup); rejectReady(new Error('Recording failed. Please retry.'));
    recordingDiagnostic('recorder_error', { recorderState: active.state });
    if (active.state !== 'inactive') active.stop();
    stopTracks();
    failure('Recording failed. Please record again or upload an audio file.');
  };
  try {
    active.start(250);
    recordingDiagnostic('recorder_started', { mimeType: active.mimeType });
  } catch (error) {
    rejectReady(new Error('Microphone recording could not start. Please retry.'));
    disposed = true; detach(); stopTracks(); throw error;
  }
  return {
    ready,
    stop() {
      if (!disposed && active.state !== 'inactive') {
        clearTimeout(startup);
        if (!started) rejectReady(new DOMException('Recording stopped before readiness.', 'AbortError'));
        active.stop();
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      rejectReady(new DOMException('Recording cancelled.', 'AbortError'));
      if (active.state !== 'inactive') active.stop();
      detach(); stopTracks();
    },
  };
}
