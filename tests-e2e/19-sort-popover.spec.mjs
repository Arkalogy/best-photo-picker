// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("19 — Toolbar Sort popover opens with options", () => {
  test("clicking the Sort button reveals sort-popover", async ({ page }) => {
    await openApp(page);

    const sortBtn = page.locator("#btn-sort");
    await expect(sortBtn).toBeVisible();
    await sortBtn.click();

    const popover = page.locator("#sort-popover");
    await expect(popover).toBeVisible({ timeout: 5000 });

    // Sanity: popover lists at least one of the well-known sort options
    const text = (await popover.textContent()) || "";
    expect(text).toMatch(/score|date|filename|size|file size/i);

    // Clicking outside closes
    await page.mouse.click(10, 10);
    await expect(popover).not.toBeVisible();
  });
});
