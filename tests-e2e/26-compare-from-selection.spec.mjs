// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("26 — Compare view opens from a 2-photo selection", () => {
  test("Cmd-click two cards → batch Compare button → compare overlay", async ({ page }) => {
    await openApp(page);

    const cards = page.locator("#photo-grid .card");
    await expect(cards.first()).toBeVisible({ timeout: 15000 });
    test.skip((await cards.count()) < 2, "need ≥ 2 cards to compare");

    await cards.nth(0).click({ modifiers: ["Meta"] });
    await cards.nth(1).click({ modifiers: ["Meta"] });

    const compareBtn = page.locator("#batch-compare-btn");
    await expect(compareBtn).toBeVisible({ timeout: 5000 });
    await compareBtn.click();

    const compare = page.locator("#compare-overlay");
    await expect(compare).toBeVisible();
    await expect(compare).toHaveClass(/visible/);

    // VERACITY: compare view renders BOTH images, not just the overlay
    const compareImgs = compare.locator("img");
    expect(await compareImgs.count()).toBeGreaterThanOrEqual(2);

    // Esc closes
    await page.keyboard.press("Escape");
    await expect(compare).not.toHaveClass(/visible/);
  });
});
