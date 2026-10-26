import { defineConfig, devices } from '@playwright/test';

/** Real local API and recording upload; only microphone/SDK outputs are prescribed. */
export default defineConfig({
  testDir: './tests', testMatch: 'short-choice.spec.ts', timeout: 60000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:3103' },
  projects: [
    { name: 'short-choice-chromium', use: { browserName: 'chromium' } },
    { name: 'short-choice-webkit', use: { ...devices['iPhone 13'], browserName: 'webkit' } },
  ],
  webServer: [
    { command: '../.venv/bin/python -m uvicorn --app-dir .. tests.short_choice_acceptance_server:app --loop asyncio --host 127.0.0.1 --port 8103',
      url: 'http://127.0.0.1:8103/docs', timeout: 60000, reuseExistingServer: false },
    { command: 'npm run dev -- --port 3103 --webpack', url: 'http://127.0.0.1:3103', timeout: 300000, reuseExistingServer: false,
      env: { AUDLI_BROWSER_TEST: '1', BACKEND_URL: 'http://127.0.0.1:8103',
        NEXT_PUBLIC_SUPABASE_URL: '', NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: '' } },
  ],
});
