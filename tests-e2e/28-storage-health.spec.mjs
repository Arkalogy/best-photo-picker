// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("28 — /api/health/storage reports library reachability", () => {
  test("health endpoint returns ok-shaped response when library is mounted", async ({ page }) => {
    await openApp(page);

    const health = await api(page, "/api/v1/health/storage");

    expect(typeof health).toBe("object");
    // bp_core.py:473 returns {accessible: bool, ...}. Library is local
    // for e2e, so accessible must be true.
    expect(health.accessible).toBe(true);
  });
});
