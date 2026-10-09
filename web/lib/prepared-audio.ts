import type { LessonOperation } from './api';

/** One operation owns one preparation and one playback. No URL exists before playback.
 * Capture failures immediately: background work must never reject unobserved. */
export class PreparedAudio {
  private result: Promise<{ ok: true; body: Blob } | { ok: false; error: unknown }>;
  private playback: Promise<void> | null = null;
  private operation: LessonOperation;
  constructor(operation: LessonOperation, prepare: () => Promise<Blob>) {
    this.operation = operation;
    this.result = operation.wait(prepare).then(
      body => ({ ok: true as const, body }),
      error => ({ ok: false as const, error }),
    );
  }
  play(play: (body: Blob) => Promise<void>, validate: () => Promise<void>): Promise<void> {
    this.operation.assertCurrent();
    if (this.playback) return this.playback;
    this.playback = (async () => {
      const result = await this.result;
      this.operation.assertCurrent();
      if (!result.ok) throw result.error;
      await validate();
      this.operation.assertCurrent();
      await play(result.body);
      this.operation.assertCurrent();
    })();
    return this.playback;
  }
}
