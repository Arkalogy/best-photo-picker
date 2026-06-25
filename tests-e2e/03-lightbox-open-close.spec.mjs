// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("03 — Lightbox opens, navigates, and closes", () => {
  test("clicking a card opens the lightbox; arrows nav; Esc closes", async ({ page }) => {
    await openApp(page);

    const firstCard = page.locator("#photo-grid .card").first();
    await expect(firstCard).toBeVisible({ timeout: 15000 });
    await firstCard.click();

    const lightbox = page.locator("#lightbox");
    await expect(lightbox).toBeVisible();
    await expect(lightbox).toHaveClass(/visible/);

    // VERACITY: capture the current photo's src, advance, assert it
    // actually changed. "Lightbox still visible" alone passes even if
    // ArrowRight is a no-op.
    const lbImg = page.locator("#lb-img");
    const firstSrc = await lbImg.getAttribute("src");
    expect(firstSrc).toBeTruthy();
    await page.keyboard.press("ArrowRight");
    await expect.poll(async () => lbImg.getAttribute("src"), { timeout: 5000 }).not.toBe(firstSrc);
    await expect(lightbox).toHaveClass(/visible/);

    // Esc closes
    await page.keyboard.press("Escape");
    await expect(lightbox).not.toHaveClass(/visible/);
  });
});
