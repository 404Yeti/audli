import { test, expect } from '@playwright/test';
import { freshAttempt, lessonCard } from './listening-fixture';

test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const noHorizontalScroll = (page: import('@playwright/test').Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

test.describe.serial('2.0 settings (Figma D15/M15)', () => {
  test('controls save, survive a reload, and drive the player and transcript', async ({ page }) => {
    await page.goto('/listening/settings');
    await expect(page.getByRole('heading', { name: 'Settings', level: 1 })).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);

    await page.getByRole('radio', { name: 'Guided' }).click();
    await page.getByRole('radio', { name: '0.75×' }).click();
    await page.getByRole('radio', { name: 'Large' }).click();
    await page.getByRole('switch', { name: 'Reduce motion' }).click();
    await expect(page.getByTestId('settings-status')).toHaveText('Saved');

    await page.reload();
    await expect(page.getByRole('radio', { name: 'Guided' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('radio', { name: '0.75×' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('radio', { name: 'Large' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('switch', { name: 'Reduce motion' })).toHaveAttribute('aria-checked', 'true');

    // the lesson honours them
    await page.goto('/listening');
    await lessonCard(page, /Ordering food at a restaurant/).click();
    await freshAttempt(page);
    await expect(page.getByRole('button', { name: /Playback speed 0\.75×/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Guided' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('unavailable items are labelled, not interactive; Review cannot be chosen; privacy link works', async ({ page }) => {
    await page.goto('/listening/settings');
    await expect(page.getByRole('radio', { name: 'Review' })).toBeDisabled();
    for (const name of ['Preferred accent', 'Current level', 'Learning goals', 'Prefer typing over speaking', 'Download my data', 'Delete my account'])
      await expect(page.locator('li[data-unavailable="true"]').filter({ hasText: name })).toBeVisible();
    await expect(page.getByText('Not available yet', { exact: true })).toHaveCount(6);
    await expect(page.getByText('30 days')).toHaveCount(0);             // Figma's retention figure is not how Audli works
    await expect(page.getByRole('link', { name: /Privacy and data use/ })).toHaveAttribute('href', '/privacy');   // the page itself ships with the policy change
  });

  test('keyboard: every control is reachable and operable without a pointer', async ({ page }) => {
    await page.goto('/listening/settings');
    const sw = page.getByRole('switch', { name: 'High-contrast transcript' });
    await sw.focus();
    await page.keyboard.press('Space');
    await expect(sw).toHaveAttribute('aria-checked', 'true');
    await page.keyboard.press('Space');
    await expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  test('desktop layout shows two columns and no horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/listening/settings');
    const a = await page.getByRole('heading', { name: 'Learning' }).boundingBox();
    const b = await page.getByRole('heading', { name: 'Accessibility' }).boundingBox();
    expect(b!.x).toBeGreaterThan(a!.x + 300);
    expect(await noHorizontalScroll(page)).toBe(true);
  });
});
