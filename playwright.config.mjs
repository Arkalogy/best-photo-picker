// @ts-check
import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for end-to-end smoke tests against the local dev
 * server. Tests run against http://localhost:5001 — start the server
 * yourself before `npm run test:e2e` (we don't auto-spawn because the
 * dev server expects a populated library; the e2e suite is a smoke
 * layer over real data, not a fresh fixture).
 */
export default defineConfig({
  testDir: "./tests-e2e",
  testMatch: "**/*.spec.mjs",
  fullyParallel: false, // single worker — tests share live server state
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? "github" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:5001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The server requires an auth token. We grab it from /api/state on
    // first navigate and stash it in localStorage — most code paths
    // pick it up automatically, and we patch the meta tag below.
    extraHTTPHeaders: {},
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
