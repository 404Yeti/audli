import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Own page routes (controlled responses): only runs under listening.playwright.config.ts, never in the default mocked suite.
test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const SPEC = { topic: 'ordering coffee in New York', cefr: 'B1', accent: 'american', minutes: 2, format: 'dialogue', scenario: '' };
const REQUEST = '99999999-9999-4999-8999-999999999999';
const view = (extra: Record<string, unknown>) => ({ id: REQUEST, kind: 'generate', phase: 'queued', spec: SPEC, notes: [], question: null, title: null, lesson_id: null, failure: null, unavailable_reason: null, updated_at: null, ...extra });
const AVAILABLE = { available: true, reason: null, max_minutes: 2, accents: ['american', 'british', 'australian'], per_learner_daily: 2 };
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const available = (page: Page, body: unknown = AVAILABLE) => page.route('**/api/listening/generation/availability', route => route.fulfill({ json: body }));

test.describe('AI lesson generator screens (real pages, controlled responses)', () => {
  test('describe a lesson, review the interpreted settings, create it, watch it build, and open it when ready', async ({ page }) => {
    await available(page);
    let posted: any = null;
    let polls = 0;
    await page.route('**/api/listening/generation/interpret', route => route.fulfill({ json: view({ id: 'int-1', kind: 'interpret', phase: 'interpreted', notes: ['minutes_limited:2', 'defaulted:format'] }) }));
    await page.route('**/api/listening/generation/requests', async route => { posted = route.request().postDataJSON(); await route.fulfill({ json: view({ phase: 'queued' }) }); });
    await page.route(`**/api/listening/generation/requests/${REQUEST}`, route => {
      polls += 1;
      return route.fulfill({ json: polls < 2 ? view({ phase: 'script' }) : polls < 3 ? view({ phase: 'audio', title: 'Ordering coffee in New York' }) : view({ phase: 'ready', title: 'Ordering coffee in New York', lesson_id: 'lesson-1' }) });
    });
    await page.goto('/listening/create');
    await expect(page.getByRole('heading', { name: 'Create a lesson' })).toBeVisible();
    await page.getByLabel('What do you want to listen to?').fill('Create a B1 listening lesson about ordering coffee in New York, with American accents, around three minutes long.');
    await page.getByRole('button', { name: 'Fill in the details' }).click();                       // the first click only reads the request: nothing is created yet
    await expect(page.getByText('We filled in the details below from your description')).toBeVisible();
    await expect(page.getByText('Lessons are up to 2 minutes for now, so we shortened it.')).toBeVisible();
    expect(posted).toBeNull();
    await expect(page.getByRole('radio', { name: 'B1', exact: true })).toBeChecked();
    await page.getByRole('radio', { name: /British/ }).click();                                    // the learner adjusts the settings before generating
    await page.getByRole('button', { name: 'Create lesson' }).click();
    await expect(page.getByRole('status', { name: 'Lesson progress' })).toBeVisible();
    expect(posted.spec).toMatchObject({ topic: 'ordering coffee in New York', accent: 'british', minutes: 2, format: 'dialogue' });
    expect(Object.keys(posted).sort()).toEqual(['client_request_id', 'spec', 'text']);
    await expect(page.getByText('Saved to your library')).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('link', { name: 'Start listening' })).toHaveAttribute('href', '/listening/lesson-1');
    await expect(page.getByText('4 exercises')).toBeVisible();
  });

  test('the structured controls alone create a lesson and an unavailable accent cannot be chosen', async ({ page }) => {
    await available(page);
    await page.route('**/api/listening/generation/interpret', route => route.fulfill({ json: view({ id: 'int-2', kind: 'interpret', phase: 'interpreted' }) }));
    let posted: any = null;
    await page.route('**/api/listening/generation/requests', async route => { posted = route.request().postDataJSON(); await route.fulfill({ json: view({ phase: 'queued' }) }); });
    await page.route(`**/api/listening/generation/requests/${REQUEST}`, route => route.fulfill({ json: view({ phase: 'script' }) }));
    await page.goto('/listening/create');
    await expect(page.getByRole('button', { name: 'Create lesson' })).toBeDisabled();             // nothing to build yet
    await page.getByRole('button', { name: 'Airport delay' }).click();
    await page.getByRole('radio', { name: /Australian/ }).click();
    await page.getByRole('radio', { name: 'Story' }).click();
    await page.getByRole('radio', { name: '~1 min' }).click();
    await expect(page.getByText('Coming soon: Irish, Indian')).toBeVisible();
    await expect(page.getByRole('radio', { name: /Irish/ })).toHaveCount(0);                      // unsupported accents are not selectable at all
    await expect(page.getByText('Longer · coming soon')).toBeVisible();
    await page.getByRole('button', { name: 'Fill in the details' }).click();                      // the same pipeline: the description is read and reviewed first
    await expect(page.getByText('We filled in the details below')).toBeVisible();
    await page.getByRole('button', { name: 'Create lesson' }).click();
    await expect(page.getByRole('status', { name: 'Lesson progress' })).toBeVisible();
    expect(posted.spec.cefr).toBe('B1');
  });

  test('a request with no usable topic gets one question, and a refused request gets the not-supported state', async ({ page }) => {
    await available(page);
    await page.route('**/api/listening/generation/interpret', route => route.fulfill({ json: view({ id: 'int-3', kind: 'interpret', phase: 'needs_input', spec: null, question: 'What would you like the lesson to be about?' }) }));
    await page.goto('/listening/create');
    await page.getByLabel('What do you want to listen to?').fill('make me something');
    await page.getByRole('button', { name: 'Fill in the details' }).click();
    await expect(page.getByText('What would you like the lesson to be about?')).toBeVisible();
    await page.unroute('**/api/listening/generation/interpret');
    await page.route('**/api/listening/generation/interpret', route => route.fulfill({ json: view({ id: 'int-4', kind: 'interpret', phase: 'rejected', spec: null }) }));
    await page.getByRole('button', { name: 'Fill in the details' }).click();
    await expect(page.getByRole('heading', { name: 'We can’t make that lesson' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit request' }).click();
    await expect(page.getByLabel('What do you want to listen to?')).toHaveValue('make me something');          // nothing the learner typed is lost
  });

  test('a failed lesson keeps the request, offers Try again and Edit request, and never shows the lesson as ready', async ({ page }) => {
    await available(page);
    const posts: any[] = [];
    await page.route('**/api/listening/generation/interpret', route => route.fulfill({ json: view({ id: 'int-5', kind: 'interpret', phase: 'interpreted' }) }));
    await page.route('**/api/listening/generation/requests', async route => { posts.push(route.request().postDataJSON()); await route.fulfill({ json: view({ phase: 'queued' }) }); });
    await page.route(`**/api/listening/generation/requests/${REQUEST}`, route => route.fulfill({ json: view({ phase: 'failed', failure: 'The audio didn’t pass our quality checks. Nothing was saved. Please try again.' }) }));
    await page.goto('/listening/create');
    await page.getByLabel('What do you want to listen to?').fill('a train booking');
    await page.getByRole('button', { name: 'Fill in the details' }).click();
    await page.getByRole('button', { name: 'Create lesson' }).click();
    await expect(page.getByRole('heading', { name: 'We couldn’t finish this lesson' })).toBeVisible();
    await expect(page.getByText('Nothing was saved')).toBeVisible();
    await expect(page.getByText('Saved to your library')).toHaveCount(0);
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect.poll(() => posts.length).toBe(2);
    expect(posts[1].spec).toEqual(posts[0].spec);                                                  // the same settings; a new request id
    expect(posts[1].client_request_id).not.toBe(posts[0].client_request_id);
  });

  test('when limits are used up the unavailable state is shown instead of a form, in plain words', async ({ page }) => {
    await available(page, { ...AVAILABLE, available: false, reason: 'daily_limit' });
    await page.goto('/listening/create');
    await expect(page.getByRole('heading', { name: 'That’s today’s lessons made' })).toBeVisible();
    await expect(page.getByText('You can create 2 lessons a day.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create lesson' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Browse library' })).toBeVisible();
    for (const word of ['credit', 'dollar', 'elevenlabs', 'openai', 'quota']) await expect(page.getByText(new RegExp(word, 'i'))).toHaveCount(0);
  });

  test('a lesson that takes more than a minute says so and still lets the learner leave', async ({ page }) => {
    await available(page);
    await page.clock.install();
    await page.route('**/api/listening/generation/requests/*', route => route.fulfill({ json: view({ phase: 'audio' }) }));
    await page.addInitScript(id => sessionStorage.setItem('audli.generation.current', id), REQUEST);
    await page.goto('/listening/create');
    await expect(page.getByRole('status', { name: 'Lesson progress' })).toBeVisible();
    await page.clock.fastForward(65_000);
    await expect(page.getByRole('heading', { name: 'Still building your lesson' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Browse while you wait' })).toBeVisible();
  });

  test('every generator screen fits a phone, a small phone and a desktop and has no accessibility violations', async ({ page }) => {
    await available(page);
    await page.route(`**/api/listening/generation/requests/${REQUEST}`, route => route.fulfill({ json: view({ phase: 'ready', title: 'Ordering coffee in New York', lesson_id: 'l1' }) }));
    await page.goto('/listening/create');
    for (const [width, height] of [[390, 844], [320, 640], [1440, 900]]) {
      await page.setViewportSize({ width, height });
      expect(await noOverflow(page), `form at ${width}`).toBe(true);
    }
    const form = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(form.violations.map(v => `${v.id} ${v.nodes.map(n => n.target).join(' | ')}`)).toEqual([]);
    await page.addInitScript(id => sessionStorage.setItem('audli.generation.current', id), REQUEST);
    await page.goto('/listening/create');
    await expect(page.getByText('Saved to your library')).toBeVisible();
    expect(await noOverflow(page)).toBe(true);
    const ready = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(ready.violations.map(v => `${v.id} ${v.nodes.map(n => n.target).join(' | ')}`)).toEqual([]);
  });
});
