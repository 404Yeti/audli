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
  const chunks: Blob[] = [];
  const stopTracks = () => input.getTracks().forEach(track => track.stop());
  const detach = () => { active.ondataavailable = null; active.onstop = null; active.onerror = null; chunks.length = 0; };
  active.ondataavailable = event => {
    if (disposed || failed || !event.data.size) return;
    chunks.push(event.data);
    recordingDiagnostic('chunk_received', { chunkCount: chunks.length, chunkSize: event.data.size, mimeType: event.data.type });
  };
  active.onstop = () => {
    stopTracks();
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
    recordingDiagnostic('recorder_error', { recorderState: active.state });
    if (active.state !== 'inactive') active.stop();
    stopTracks();
    failure('Recording failed. Please record again or upload an audio file.');
  };
  try {
    active.start(250);
    recordingDiagnostic('recorder_started', { mimeType: active.mimeType });
  } catch (error) {
    disposed = true; detach(); stopTracks(); throw error;
  }
  return {
    stop() { if (!disposed && active.state !== 'inactive') active.stop(); },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (active.state !== 'inactive') active.stop();
      detach(); stopTracks();
    },
  };
}
