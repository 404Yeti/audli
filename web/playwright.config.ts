import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:3100', viewport: { width: 390, height: 844 } },
  webServer: { command: 'npm run dev -- --port 3100', url: 'http://127.0.0.1:3100', reuseExistingServer: false, timeout: 120000,
    env: { AUDLI_BROWSER_TEST: '1', NEXT_PUBLIC_SUPABASE_URL: 'https://audli-auth-test.supabase.co', NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_browser_fixture' } },
});
