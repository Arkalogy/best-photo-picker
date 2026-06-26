// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("31 — Photo grid uses lazy-loading for thumbnails", () => {
  test("img tags inside cards have loading='lazy'", async ({ page }) => {
    await openApp(page);

    const cards = page.locator("#photo-grid .card");
    await expect(cards.first()).toBeVisible({ timeout: 15000 });

    // Sample a handful — every card image should be lazy-loaded so a
    // 10K-photo library doesn't blow memory.
    const imgs = cards.locator("img");
    const sample = Math.min(5, await imgs.count());
    for (let i = 0; i < sample; i++) {
      const loading = await imgs.nth(i).getAttribute("loading");
      expect(loading, `card ${i} img should be loading=lazy`).toBe("lazy");
    }
  });
});
