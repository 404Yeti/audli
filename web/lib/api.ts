export async function api<T>(url: string, init?: RequestInit, onResponse?: (status: number) => void): Promise<T> {
  const response = await fetch(`/api${url}`, { ...init, cache: 'no-store' });
  onResponse?.(response.status);
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    // The Next.js proxy can return HTML/plain text when FastAPI is unreachable.
    throw new Error('Audli could not reach the server. Check that FastAPI is running and try again.');
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new Error(typeof detail === 'string' ? detail : 'Please check your input and try again.');
  }
  return data as T;
}
