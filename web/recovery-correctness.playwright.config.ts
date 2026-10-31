import { defineConfig, devices } from '@playwright/test';
import base from './playwright.config';
export default defineConfig({ ...base, testMatch: ['structured-lesson.spec.ts'], grep: /delayed assessment|rejected assessment|optional acknowledgment|assessment retry|End during delayed reflection/, projects: [
  { name: 'chromium', use: { browserName: 'chromium' } },
  { name: 'webkit-iphone', use: { ...devices['iPhone 13'], browserName: 'webkit' } },
] });
