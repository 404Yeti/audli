import { defineConfig, devices } from '@playwright/test';
import base from './playwright.config';
export default defineConfig({
  ...base, testMatch: ['upload-recovery.spec.ts'],
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'webkit-iphone', use: { ...devices['iPhone 13'], browserName: 'webkit' } },
  ],
});
