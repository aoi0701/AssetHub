import { defineConfig, devices } from '@playwright/test';

// Own port and server: never reuses the week 4 dev server on 5173. Every /api/** call is mocked in the spec.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'assistant.spec.js',
  timeout: 60000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5176',
    trace: 'off',
    screenshot: 'off',
    headless: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev -- --port 5176 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:5176',
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      VITE_ASSISTANT_ENABLED: 'true',
      // Unreachable on purpose: a request that escapes the mocks fails instead of hitting a real backend.
      VITE_PROXY_TARGET: 'http://127.0.0.1:9',
    },
  },
});
