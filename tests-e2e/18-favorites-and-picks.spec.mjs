// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("18 — Favorites and BPP Picks navigation", () => {
  test("Favorites nav switches the filter to 'favorites'", async ({ page }) => {
    await openApp(page);

    const favNav = page.locator('.sidebar .nav-item[data-action*="navigateToFavorites"]').first();
    test.skip(!(await favNav.isVisible().catch(() => false)), "no Favorites nav");
    await favNav.click();

    // Favorites is implemented as the "all" album + a filter override —
    // not a separate view — so the toolbar title stays "Library" and
    // we assert the filter dropdown moved instead.
    const filterBy = page.locator("#filter-by");
    await expect(filterBy).toHaveValue("favorites", { timeout: 5000 });
    await expect(page.locator("#photo-grid")).not.toHaveClass(/hidden/);
  });

  test("BPP Picks nav activates the Picks sidebar item", async ({ page }) => {
    await openApp(page);

    // BPP Picks is rendered as a sub-item under Library (class
    // `.nav-subitem-picks` with action `navigateToLibraryPicks`).
    // The old selector searched for `.nav-item[onclick*="navigateToPicks"]`
    // which never matched and silently skipped on every library.
    const picksNav = page.locator(".sidebar .nav-subitem-picks").first();
    test.skip(!(await picksNav.isVisible().catch(() => false)), "no Picks nav");
    await picksNav.click();

    // Sub-item gets `.active` when Picks is the current view.
    await expect(picksNav).toHaveClass(/active/, { timeout: 10000 });
    await expect(page.locator("#photo-grid")).not.toHaveClass(/hidden/);
  });
});
