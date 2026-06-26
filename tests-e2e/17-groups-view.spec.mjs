// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("17 — Groups view loads when face co-occurrence groups exist", () => {
  test("clicking Groups in sidebar opens the groups view", async ({ page }) => {
    await openApp(page);

    const groupsNav = page.locator('.sidebar .nav-item[data-action*="navigateToGroups"]').first();
    test.skip(
      !(await groupsNav.isVisible().catch(() => false)),
      "no Groups nav — no co-occurrence groups detected"
    );
    await groupsNav.click();

    await expect(page.locator("#toolbar-title")).toContainText(/Groups/i, {
      timeout: 5000,
    });
    await expect(page.locator("#groups-view")).toBeVisible({ timeout: 10000 });
  });
});
