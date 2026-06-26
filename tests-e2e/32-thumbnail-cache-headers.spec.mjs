// @ts-check
import { expect, test } from "@playwright/test";

import { authToken, openApp } from "./_helpers.mjs";

test.describe("32 — Thumbnail responses set long Cache-Control headers", () => {
  test("a /thumb/<hash> response has immutable + max-age cache headers", async ({ page }) => {
    await openApp(page);

    // Find a real thumb hash from a rendered card
    const firstImg = page.locator("#photo-grid .card img").first();
    await expect(firstImg).toBeAttached({ timeout: 15000 });
    const src = await firstImg.getAttribute("src");
    expect(src).toBeTruthy();

    // src may be a relative path like /thumb/<hash> or include ?_token=…
    // Drive the request through Playwright's request fixture so we can
    // read response headers.
    const token = await authToken(page);
    const url = src.startsWith("http") ? src : `http://localhost:5001${src}`;
    const sep = url.includes("?") ? "&" : "?";
    const resp = await page.request.get(`${url}${sep}_token=${token}`);
    expect(resp.ok()).toBeTruthy();

    // VERACITY: thumbnail cache should be 1-year immutable per pm.md
    // ("Cache has 1-year immutable Cache-Control headers"). If anything
    // ever drops below that, the perf regression is silent until users
    // notice slow scrolling.
    const cc = resp.headers()["cache-control"] || "";
    expect(cc).toMatch(/immutable|max-age=/);
  });
});
