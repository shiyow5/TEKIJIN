import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E for the TEKIJIN frontend.
 *
 * Tests run against a PRODUCTION build (`next build && next start`) so they
 * exercise the same output CI ships. All backend traffic is mocked at the
 * network layer (see e2e/support/mocks.ts) — no live API / DB / LLM / embeddings
 * — so the suite is deterministic and hermetic.
 */

const PORT = Number(process.env.E2E_PORT ?? 3100);
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npm run start -- --port ${PORT}`,
    url: BASE_URL,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
    // NEXT_PUBLIC_API_BASE_URL is inlined at build time; pin it so the mock
    // route patterns (http://localhost:8000/*) always match the real requests.
    env: { NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" },
  },
});
