/** A 202 acknowledges ownership, never completed delivery. Status reads never resend audio. */
export type OperationReference = { operation_id: string; kind: string };
export class OperationProcessing extends Error {
  readonly reference: OperationReference;
  constructor(reference: OperationReference) { super('Your answer is still processing.'); this.name = 'OperationProcessing'; this.reference = reference; }
}
export function operationReference(value: unknown): OperationReference {
  if (!value || typeof value !== 'object') throw new Error('Invalid operation acknowledgment.');
  const v = value as Record<string, unknown>;
  if (typeof v.operation_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v.operation_id)
      || typeof v.kind !== 'string' || !/^[a-z_]{1,20}$/.test(v.kind)) throw new Error('Invalid operation acknowledgment.');
  return { operation_id: v.operation_id, kind: v.kind };
}
export type OperationStatus<T> = OperationReference & { status: 'processing' | 'complete' | 'uncertain' | 'invalidated'; result?: T };
function abortable<T>(task: () => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const finish = (error: unknown, value?: T) => { signal.removeEventListener('abort', abort); if (error) reject(error); else resolve(value as T); };
    const abort = () => finish(new DOMException('Cancelled.', 'AbortError'));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    Promise.resolve().then(() => { signal.throwIfAborted(); return task(); }).then(value => finish(null, value), error => finish(error));
  });
}
/** Call inside the sender's bounded stage; its abort covers the GETs and delays together. */
export async function awaitOperation<T>(reference: OperationReference,
  read: (path: string, signal: AbortSignal) => Promise<OperationStatus<T>>, signal: AbortSignal,
  pause: (signal: AbortSignal) => Promise<void>): Promise<T> {
  while (true) {
    signal.throwIfAborted();
    const value = await abortable(() => read(`/operations/${reference.operation_id}`, signal), signal);
    signal.throwIfAborted();
    if (value.operation_id !== reference.operation_id || value.kind !== reference.kind) throw new Error('Operation identity changed.');
    if (value.status === 'complete' && value.result !== undefined) return value.result;
    if (value.status !== 'processing') throw new Error('Delivery remains unconfirmed. Keep your recording.');
    await abortable(() => pause(signal), signal);
  }
}
