import { defineConfig, devices } from '@playwright/test';

/** Real FastAPI + the real separate worker process + SQLite + seeded lesson + real browser. No mocked API; AI providers are the deterministic fakes. */
process.env.AUDLI_LISTENING_E2E = '1';   // enables tests/listening.spec.ts (skipped everywhere else)
export default defineConfig({
  testDir: './tests', testMatch: ['listening.spec.ts', 'listening-journey.spec.ts', 'listening-library.spec.ts', 'listening-create.spec.ts'], timeout: 180000, workers: 1, expect: { timeout: 30000 },
  use: { baseURL: 'http://127.0.0.1:3104', viewport: { width: 390, height: 844 } },
  projects: [
    // Chromium has a fake microphone device, so the real recorder runs there. WebKit has none in CI: it uses the file-upload path.
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 }, permissions: ['microphone'],
      launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } } },
    // AUDLI_REPEAT=1 runs the suite a second time on the same server, the way CI's second browser project does.
    ...(process.env.AUDLI_REPEAT ? [{ name: 'chromium-again', dependencies: ['chromium'], use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 }, permissions: ['microphone'],
      launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } } }] : []),
    ...(process.env.AUDLI_WEBKIT || process.env.CI ? [{ name: 'webkit', use: { ...devices['Desktop Safari'], viewport: { width: 390, height: 844 } } }] : []),
  ],
  webServer: [
    { command: '../.venv/bin/uvicorn --app-dir .. tests.listening_acceptance_server:app --loop asyncio --host 127.0.0.1 --port 8104',
      url: 'http://127.0.0.1:8104/api/auth/config', timeout: 60000, reuseExistingServer: false },
    { command: 'npm run dev -- --port 3104 --webpack', url: 'http://127.0.0.1:3104', timeout: 360000, reuseExistingServer: false,
      env: { AUDLI_BROWSER_TEST: '1', BACKEND_URL: 'http://127.0.0.1:8104', NEXT_PUBLIC_AUDLI_LISTENING: 'true', NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR: 'true' } },
  ],
});
