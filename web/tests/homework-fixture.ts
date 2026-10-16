import type { Page } from '@playwright/test';
import { installVoice, promptWav } from './voice-fixture';

export async function setup(page: Page, completion = false, options: { capability?: unknown; configFailure?: boolean } = {}) {
  await installVoice(page, { autoPlayback: true });
  const state = { enabled: true, homeworkRequests: 0, configRequests: 0, status: 'offered', pending: false, decisions: 0, checks: 0, spoken: 0, confirmations: 0, voiceRequests: 0, acceptVoice: true, failVoice: false, lostResponse: false };
  const assignment = () => ({ id: 'assignment', lesson_id: 'lesson', status: state.status, discovery_in_progress: state.pending,
    objective: 'Listen for the main idea and two supporting details.', message: 'No suitable listening assignment is available right now. Your lesson is saved.',
    content: ['assigned', 'completed'].includes(state.status) ? { provider: 'youtube', provider_id: 'abcdefghijk', canonical_url: 'https://www.youtube.com/watch?v=abcdefghijk', title: 'Science in everyday life', publisher: 'Synthetic acceptance fixture', duration_seconds: 180, validated_at: new Date().toISOString() } : null });
  if (completion) await page.addInitScript(() => { if (!sessionStorage.getItem('readiness-initialized')) { sessionStorage.setItem('audli-session:local', 'lesson'); sessionStorage.setItem('readiness-initialized', '1'); } });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/api/homework') || path.endsWith('/homework')) state.homeworkRequests++;
    if (path === '/api/auth/config') {
      state.configRequests++;
      if (options.configFailure) return route.fulfill({ status: 503, json: { detail: 'Configuration unavailable' } });
      return route.fulfill({ json: { mode: 'local', homework_enabled: 'capability' in options ? options.capability : state.enabled } });
    }
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', goal: 'Meetings', completed_attempts: 3 }, provider: 'demo' } });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/lessons/lesson') return route.fulfill({ json: { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, encouragement: 'Thanks for listening, Maya.' } });
    if (path === '/api/review') return route.fulfill({ json: { summary: { completed_lessons: 0, structured_lessons: 0, earlier_practice: 0, practice_days: 0, undated_lessons: 0, timezone_used: 'UTC', timezone_fallback: false, current_practice_level: null }, insight: 'Complete a lesson to see your listening journey.', patterns_version: 'review-patterns-v1', patterns: [], lessons: [], assessed_practice: [], next_cursor: null } });
    if (path.endsWith('/topic-checkin')) return route.fulfill({ json: { offered: false, plan: null } });
    if (path === '/api/plan') return route.fulfill({ json: { revision: 1, focus: 'Notice the details that matter', readiness: 'ready', upcoming: ['Work updates', 'Travel stories', 'Science discoveries'].map((title, i) => ({ number: i + 1, title, objective: 'Listen for important details.', difficulty: { speech_rate: 1, duration_seconds: 60, vocabulary_level: 'B1', information_density: 1 } })), checkin: { available: false } } });
    if (path === '/api/homework') return route.fulfill({ json: { enabled: state.enabled, assignments: [assignment()] } });
    if (path === '/api/lessons/lesson/homework' || path === '/api/homework/assignment') return route.fulfill({ json: assignment() });
    if (path.endsWith('/confirmation-audio')) { state.confirmations++; return route.fulfill({ contentType: 'audio/wav', body: promptWav() }); }
    if (path.endsWith('/decision/attempts')) {
      state.voiceRequests++; state.decisions++;
      state.status = state.failVoice ? 'unavailable' : state.acceptVoice ? 'assigned' : 'skipped';
      return route.fulfill({ json: assignment() });
    }
    if (path.endsWith('/farewell-audio')) return route.fulfill({ contentType: 'audio/wav', body: promptWav() });
    if (path.endsWith('/invitation-audio')) { state.spoken++; return route.fulfill({ contentType: 'audio/wav', body: promptWav() }); }
    if ((path.endsWith('/decision') || path.endsWith('/decision/text'))) {
      state.decisions++; state.status = route.request().postDataJSON().text?.startsWith('no') ? 'skipped' : 'assigned';
      if (state.lostResponse) return route.abort('internetdisconnected');
      return route.fulfill({ json: assignment() });
    }
    if (path.endsWith('/validate')) { state.checks++; state.status = 'unavailable'; return route.fulfill({ json: assignment() }); }
    return route.fulfill({ json: [] });
  });
  await page.goto('/');
  if (!completion) await page.getByRole('button', { name: 'Plan', exact: true }).click();
  return state;
}
