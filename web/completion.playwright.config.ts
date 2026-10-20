import { defineConfig, devices } from '@playwright/test';
import base from './playwright.config';
export default defineConfig({ ...base, testMatch: ['completion-regressions.spec.ts', 'lifecycle.spec.ts', 'ending.spec.ts', 'ios-success.spec.ts'], projects: [
  { name: 'chromium', use: { browserName: 'chromium' } },
  { name: 'webkit-iphone', use: { ...devices['iPhone 13'], browserName: 'webkit' } },
] });
