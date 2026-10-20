import { test, expect } from '@playwright/test';
import { lifecycle, recordPhases } from './lifecycle-fixture';
import { typeReply } from './homework-fixture';

/** AUD-73: the combined Success screen on an iPhone-class WebKit viewport (also runs on Chromium).
 *  Playwright WebKit is not iOS Safari: this checks that the Rive canvas renders, animates and fits beside the statistics
 *  and Return Home in WebKit's engine; it cannot prove behaviour on a real device. Needs WebKit's system libraries,
 *  so it runs in CI (classroom-safari / completion configs) rather than on machines without them. */
test('Success renders an animating Pip beside the statistics and Return Home, then returns Home', async ({ page }) => {
  const phases = await recordPhases(page);
  await lifecycle(page, { from: 'CLOSING', due: false });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'HOMEWORK');
  // Wrap-up is never blank, even before any request settles.
  await expect(page.getByRole('heading', { name: 'Lesson completed' })).toBeVisible();
  await typeReply(page, 'no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'SUCCESS');
  const canvas = page.locator('canvas');
  await expect(canvas).toHaveCount(1);
  await canvas.evaluate(node => {
    const c = node as HTMLCanvasElement;
    const seen = new Set<string>();
    (window as unknown as { pipFrames: Set<string> }).pipFrames = seen;
    const sample = () => {
      if (!c.isConnected) return;
      if (c.width && c.height) {
        const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
        let opaque = 0, sum = 0;
        for (let i = 0; i < d.length; i += 4 * 17) if (d[i + 3] > 100) { opaque++; sum += d[i] + d[i + 1] * 3 + d[i + 2] * 7; }
        if (opaque) seen.add(opaque + ':' + sum);
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  // Pip animates (several distinct frames) rather than sitting on one static image.
  await expect.poll(() => page.evaluate(() => (window as unknown as { pipFrames: Set<string> }).pipFrames.size), { timeout: 15000 }).toBeGreaterThan(5);
  const box = (await canvas.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.width).toBeGreaterThan(viewport.width * .5);
  expect(box.x).toBeGreaterThanOrEqual(-viewport.width * .15); expect(box.x + box.width).toBeLessThanOrEqual(viewport.width * 1.15);
  // The statistics and the only way out are on the same screen, reachable by touch.
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  const home = page.getByRole('button', { name: 'Return Home' });
  await expect(home).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.waitForTimeout(1500);
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'SUCCESS');   // nothing moves it on
  await home.tap().catch(() => home.click());
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'HOME');
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
  expect(await phases()).not.toContain('STATS');
});
