import { test, expect } from '@playwright/test';
import { installVoice, promptWav, probe } from './voice-fixture';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';

test.skip(process.env.AUDLI_SHORT_CHOICE_TEST !== '1', 'Run with the isolated short-choice config/server');

for (const [text, consent] of [['Yes, please.', 'accepted'], ['Sure.', 'accepted'],
  ['Okay.', 'accepted'], ['No, thanks.', 'declined']]) {
  test(`real recorded upload of ${text} progresses once without recognition retry`, async ({ page, request }) => {
    await request.post('http://127.0.0.1:8103/__fixture/reset', { data: { text } });
    // Synthetic 700ms tone plus silence. The recorder, Blob finalization,
    // multipart upload, decoder, STT classifier and intent route all execute.
    const recording = promptWav();
    for (let i = 0; i < 5600; i++) recording.writeInt16LE(Math.round(3000 * Math.sin(2 * Math.PI * 220 * i / 8000)), 44 + i * 2);
    await installVoice(page, { autoPlayback: true, recordingWav: [...recording] });
    const uploads: LessonCheckpoint[] = [], answers: LessonCheckpoint[] = [];
    let retries = 0;
    page.on('request', r => { if (r.url().endsWith('/recognition/retry-audio')) retries++; });
    page.on('response', async response => {
      if (response.ok() && /\/lessons\/[^/]+\/attempts$/.test(response.url())) uploads.push(await response.json());
      if (response.ok() && /\/lessons\/[^/]+\/answer$/.test(response.url())) answers.push(await response.json());
    });
    // Keep the test focused on the completed check-in, before unrelated exercise generation.
    await page.route('**/api/lessons/*/heard', route => route.fulfill({ status: 409, json: { detail: 'End of isolated check-in fixture' } }));
    await page.goto('/');
    await page.getByRole('button', { name: 'Start today’s session' }).click();
    await expect.poll(() => answers.length).toBe(1);
    expect(uploads).toHaveLength(1);
    expect(uploads[0].pending?.uncertainty).toEqual(['Too little recognized speech to evaluate reliably.']);
    expect(uploads[0].pending_recognition).toBe('accepted');
    expect(answers[0].phase).toBe('WELCOME_ACK');
    expect(answers[0].pending).toBeNull();
    expect(retries).toBe(0);
    expect((await probe(page)).recorders).toBe(1);
    const metrics = await request.get('http://127.0.0.1:8103/__fixture/metrics');
    expect(await metrics.json()).toMatchObject({ transcriptions: 1, consent });
  });
}

for (const choice of [{ text: 'Yes, please.', confidence: .64 }, { text: 'Yes, no.' },
  { text: 'Okay.', offered: false }]) {
  test(`uncertain or out-of-context choice stays unaccepted: ${JSON.stringify(choice)}`, async ({ page, request }) => {
    await request.post('http://127.0.0.1:8103/__fixture/reset', { data: choice });
    const recording = promptWav();
    for (let i = 0; i < 5600; i++) recording.writeInt16LE(Math.round(3000 * Math.sin(2 * Math.PI * 220 * i / 8000)), 44 + i * 2);
    await installVoice(page, { autoPlayback: true, recordingWav: [...recording] });
    let answerRequests = 0;
    page.on('request', r => { if (/\/lessons\/[^/]+\/answer$/.test(r.url())) answerRequests++; });
    await page.goto('/');
    await page.getByRole('button', { name: 'Start today’s session' }).click();
    await expect(page.getByRole('button', { name: 'Retry conversation', exact: true })).toBeVisible({ timeout: 45000 });
    expect(answerRequests).toBe(0);
    const metrics = await request.get('http://127.0.0.1:8103/__fixture/metrics');
    expect(await metrics.json()).toMatchObject({ transcriptions: 3, phase: 'WELCOME', consent: 'none' });
  });
}

for (const [choice, consent] of [
  [{ text: 'Sure.', offered: false, clarify: true }, 'accepted'],
  [{ text: 'No, thanks.', initial_accent: 'british', clarify: true }, 'declined'],
] as const) {
  test(`short reply follows the actual clarification: ${JSON.stringify(choice)}`, async ({ page, request }) => {
    await request.post('http://127.0.0.1:8103/__fixture/reset', { data: choice });
    const recording = promptWav();
    for (let i = 0; i < 5600; i++) recording.writeInt16LE(Math.round(3000 * Math.sin(2 * Math.PI * 220 * i / 8000)), 44 + i * 2);
    await installVoice(page, { autoPlayback: true, recordingWav: [...recording] });
    const answers: LessonCheckpoint[] = [];
    let retries = 0;
    page.on('request', r => { if (r.url().endsWith('/recognition/retry-audio')) retries++; });
    page.on('response', async response => {
      if (response.ok() && /\/lessons\/[^/]+\/answer$/.test(response.url())) answers.push(await response.json());
    });
    await page.route('**/api/lessons/*/heard', route => route.fulfill({ status: 409, json: { detail: 'End of isolated check-in fixture' } }));
    await page.goto('/');
    await page.getByRole('button', { name: 'Start today’s session' }).click();
    await expect.poll(() => answers.length, { timeout: 15000 }).toBe(2);
    expect(answers[0].phase).toBe('REVIEW');
    expect(answers[0].prompt).toContain('Would you like Australian English');
    expect(answers[1].phase).toBe('REVIEW_ACK');
    expect(retries).toBe(0);
    expect((await probe(page)).recorders).toBe(2);
    const metrics = await request.get('http://127.0.0.1:8103/__fixture/metrics');
    expect(await metrics.json()).toMatchObject({ transcriptions: 2, consent });
  });
}
