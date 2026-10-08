import { defineConfig } from '@playwright/test';

/** Real HTTP/domain/media acceptance; AI and microphone input remain prescribed. */
export default defineConfig({
  testDir: './tests', testMatch: 'lesson-media.spec.ts', timeout: 360000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:3102', viewport: { width: 390, height: 844 } },
  webServer: [
    { command: '../.venv/bin/uvicorn --app-dir .. tests.lesson_acceptance_server:app --loop asyncio --host 127.0.0.1 --port 8102',
      url: 'http://127.0.0.1:8102/api/auth/config', timeout: 60000, reuseExistingServer: false },
    { command: 'npm run dev -- --port 3102', url: 'http://127.0.0.1:3102', timeout: 360000, reuseExistingServer: false,
      env: { AUDLI_BROWSER_TEST:'1', AUDLI_INTEGRATED_LESSON:'1', BACKEND_URL:'http://127.0.0.1:8102' } },
  ],
});
