import { expect, type Page } from '@playwright/test';

/** The server and learner persist across tests and browser projects, so every test first settles whatever attempt is open. */
export async function settleOpenAttempt(page: Page) {
  const api = page.request;
  const lessons = await (await api.get('/api/listening/lessons')).json();
  const current = await (await api.get(`/api/listening/lessons/${lessons[0].lesson_id}/attempts/current`)).json();
  if (!current.attempt_id) return;
  const view = await (await api.get(`/api/listening/attempts/${current.attempt_id}`)).json();
  if (view.attempt.status === 'completed') return;
  for (const e of view.exercises) {
    const url = `/api/listening/attempts/${view.attempt.id}`;
    if (e.feedback || e.fill || e.sequence || (e.spoken && e.spoken.status !== 'receiving')) continue;
    if (e.kind === 'spoken') await api.post(`${url}/exercises/${e.id}/skip`);
    else await api.post(`${url}/answers`, { data: { exercise_id: e.id, ...(e.kind === 'multiple_choice' ? { option_id: e.options[0].id }
      : e.kind === 'fill_blank' ? { text: 'x' } : { order: e.options.map((o: { id: string }) => o.id) }) } });
  }
}

/** Start from a new, unanswered attempt whatever the previous test left behind. */
export async function freshAttempt(page: Page) {
  await expect(page).toHaveURL(/\/listening\/[0-9a-f-]{36}/);      // a click may still be navigating: do not reload the catalog by mistake
  await settleOpenAttempt(page);
  await page.reload();
  const again = page.getByRole('button', { name: 'Listen again' });
  const start = page.getByRole('button', { name: 'Start exercises' });
  await expect(again.or(start)).toBeVisible();
  if (await again.isVisible()) await again.click();
  await expect(start).toBeVisible();
}
