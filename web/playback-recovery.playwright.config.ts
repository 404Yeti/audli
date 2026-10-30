import { defineConfig, devices } from '@playwright/test';
import base from './playwright.config';

export default defineConfig({
  ...base,
  testMatch: 'structured-lesson.spec.ts',
  grep: /AUD-95/,
  use: { ...devices['iPhone 13'], baseURL: 'http://127.0.0.1:3100' },
  projects: [{ name: 'webkit-iphone', use: { browserName: 'webkit' } }],
});
