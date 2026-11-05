// API calls for the AI Lesson Generator. Authentication comes from the shared token provider behind api().
import { api } from './api.ts';
import { newRequestId } from './listening-core.ts';
import type { Availability, GenerationView, Spec } from './generation-core.ts';

const json = { 'Content-Type': 'application/json' };
export const getAvailability = () => api<Availability>('/listening/generation/availability');
export const interpretRequest = (text: string, requestId: string = newRequestId()) =>
  api<GenerationView>('/listening/generation/interpret', { method: 'POST', headers: json, body: JSON.stringify({ client_request_id: requestId, text }) });
export const createGeneration = (spec: Spec, text: string | null, requestId: string = newRequestId()) =>
  api<GenerationView>('/listening/generation/requests', { method: 'POST', headers: json, body: JSON.stringify({ client_request_id: requestId, spec, text: text || null }) });
export const getGeneration = (id: string) => api<GenerationView>(`/listening/generation/requests/${encodeURIComponent(id)}`);

const KEY = 'audli.generation.current';
export function rememberRequest(id: string | null) { try { id ? sessionStorage.setItem(KEY, id) : sessionStorage.removeItem(KEY); } catch { /* per-viewer convenience only */ } }
export function rememberedRequest(): string | null { try { return sessionStorage.getItem(KEY); } catch { return null; } }
