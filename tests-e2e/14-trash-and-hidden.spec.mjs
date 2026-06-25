// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("14 — Recently Deleted and Hidden albums render without errors", () => {
  test("Recently Deleted nav opens the deleted view", async ({ page }) => {
    await openApp(page);

    const trashNav = page.locator('.sidebar .nav-item[data-action*="navigateToDeleted"]').first();
    test.skip(!(await trashNav.isVisible().catch(() => false)), "no Recently Deleted nav");
    await trashNav.click();

    // Toolbar updates is the most reliable signal that the nav fired
    await expect(page.locator("#toolbar-title")).toContainText(/Recently Deleted/i, {
      timeout: 5000,
    });
    // Photo grid container becomes the active view (its hidden flag is
    // dropped after loadDeletedPhotos finishes — see deleted.js).
    await expect(page.locator("#photo-grid")).not.toHaveClass(/hidden/, {
      timeout: 10000,
    });
  });

  test("Hidden nav opens the hidden view", async ({ page }) => {
    await openApp(page);

    const hiddenNav = page.locator('.sidebar .nav-item[data-action*="navigateToHidden"]').first();
    test.skip(!(await hiddenNav.isVisible().catch(() => false)), "no Hidden nav");
    await hiddenNav.click();

    await expect(page.locator("#toolbar-title")).toContainText(/Hidden/i, {
      timeout: 5000,
    });
    await expect(page.locator("#photo-grid")).not.toHaveClass(/hidden/, {
      timeout: 10000,
    });
  });
});
