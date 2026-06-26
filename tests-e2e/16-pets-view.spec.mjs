// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("16 — Pets view loads when pets are detected", () => {
  test("clicking Pets in sidebar opens the pets view", async ({ page }) => {
    await openApp(page);

    const petsNav = page.locator('.sidebar .nav-item[data-action*="navigateToPets"]').first();
    test.skip(
      !(await petsNav.isVisible().catch(() => false)),
      "no Pets nav — no cats/dogs detected"
    );
    await petsNav.click();

    await expect(page.locator("#toolbar-title")).toContainText(/Pets/i, {
      timeout: 5000,
    });
    await expect(page.locator("#pets-view")).toBeVisible({ timeout: 10000 });
  });
});
