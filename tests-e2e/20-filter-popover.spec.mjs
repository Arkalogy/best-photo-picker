// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("20 — Toolbar Filter popover opens with options", () => {
  test("clicking the Filter button reveals filter-popover", async ({ page }) => {
    await openApp(page);

    const filterBtn = page.locator("#btn-filter");
    await expect(filterBtn).toBeVisible();
    await filterBtn.click();

    const popover = page.locator("#filter-popover");
    await expect(popover).toBeVisible({ timeout: 5000 });

    // Filter offers the documented presets
    const text = (await popover.textContent()) || "";
    expect(text).toMatch(/all|favorites|selected|excluded|picks|deleted/i);

    await page.mouse.click(10, 10);
    await expect(popover).not.toBeVisible();
  });
});
