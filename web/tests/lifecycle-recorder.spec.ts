import { test, expect, type Page } from '@playwright/test';
import { recordPhases } from './lifecycle-fixture';

/** The phase recorder is test infrastructure for lifecycle.spec.ts. Its sampler is timer-driven, so these tests pin the
 *  property the lifecycle assertions rely on: a phase that is on screen when the log is read is always in the log.
 *  They use a plain served page (no application code) so the result cannot depend on app timing. */
const PROBE = '/__recorder-probe';
async function probe(page: Page) {
  await page.route('**' + PROBE, route => route.fulfill({ contentType: 'text/html', body: '<html><body>probe</body></html>' }));
  await page.goto(PROBE);
}
const setPhase = (page: Page, phase: string) => page.evaluate(value => { document.documentElement.dataset.lifecyclePhase = value; }, phase);
/** What the sampler itself has recorded (without the live phase that phases() adds), to wait for it deterministically. */
const sampled = (page: Page) => page.evaluate(() => JSON.parse(sessionStorage.getItem('phase-log') ?? '[]') as string[]);

test('the log reports the phase on screen even if the timer-driven sampler never ran', async ({ page }) => {
  // Neutralise timers before the recorder installs its sampler: it can never fire, which is the worst case for a sampled log.
  await page.addInitScript(() => { window.setInterval = (() => 0) as unknown as typeof window.setInterval; });
  const phases = await recordPhases(page);
  await probe(page);
  expect(await phases()).toEqual([]);                      // nothing on screen yet
  await setPhase(page, 'STATS');
  expect(await phases()).toEqual(['STATS']);               // sampler silent, live phase still reported
  await setPhase(page, 'HOME');
  expect(await phases()).toEqual(['HOME']);                // documents the limit: earlier unsampled phases are not reconstructed
});

test('earlier phases keep their order and the live phase is appended exactly once', async ({ page }) => {
  const phases = await recordPhases(page);                 // normal 10 ms sampler
  await probe(page);
  await setPhase(page, 'SUCCESS');
  await expect.poll(() => sampled(page)).toEqual(['SUCCESS']);          // wait until the SAMPLER has recorded it
  await setPhase(page, 'STATS');
  await expect.poll(() => sampled(page)).toEqual(['SUCCESS', 'STATS']);
  await setPhase(page, 'HOME');
  // Read immediately: the sampler may or may not have seen HOME yet. Either way the result is the same, with no duplicate.
  expect(await phases()).toEqual(['SUCCESS', 'STATS', 'HOME']);
  expect(await phases()).toEqual(['SUCCESS', 'STATS', 'HOME']);
});

test('a phase that has not changed is never duplicated', async ({ page }) => {
  const phases = await recordPhases(page);
  await probe(page);
  await setPhase(page, 'HOME');
  await expect.poll(() => sampled(page)).toEqual(['HOME']);
  for (let i = 0; i < 5; i += 1) expect(await phases()).toEqual(['HOME']);
});
