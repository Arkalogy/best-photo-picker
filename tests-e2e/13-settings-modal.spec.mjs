// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("13 — Settings modal opens via toolbar", () => {
  test("clicking the gear icon shows tabs (App / Library / Scoring / Advanced)", async ({
    page,
  }) => {
    await openApp(page);

    const gear = page.locator("#btn-settings-toolbar");
    await expect(gear).toBeVisible();
    await gear.click();

    const modal = page.locator("#settings-overlay");
    await expect(modal).toBeVisible({ timeout: 5000 });
    await expect(modal).toHaveClass(/visible/);

    // Settings has 4 tabs: App, Library, Scoring, Advanced
    for (const label of ["App", "Library", "Scoring", "Advanced"]) {
      await expect(modal).toContainText(label);
    }

    // Esc should close
    await page.keyboard.press("Escape");
    await expect(modal).not.toHaveClass(/visible/);
  });
});
