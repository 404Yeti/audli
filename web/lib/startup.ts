/** A real startup deadline, never a minimum display time. Callers cancel their
 * operation in finally, preventing late responses from changing routed state. */
export const STARTUP_TIMEOUT_MS = 20_000;
export async function startup<T>(task: () => Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([task(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('Your listening space is taking too long to connect. Please try again.')), STARTUP_TIMEOUT_MS);
    })]);
  } finally { clearTimeout(timer); }
}
