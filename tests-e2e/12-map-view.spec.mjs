// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("12 — Map view loads (Leaflet tiles)", () => {
  test("clicking Map in sidebar renders the map container", async ({ page }) => {
    await openApp(page);

    const mapNav = page.locator('.sidebar .nav-item[data-action*="navigateToMap"]').first();
    test.skip(!(await mapNav.isVisible().catch(() => false)), "no Map nav item");
    await mapNav.click();

    await expect(page.locator("#toolbar-title")).toContainText(/Map/i, {
      timeout: 5000,
    });

    // Map container exists and has a Leaflet container child once tiles
    // start loading. Don't wait for tiles to fully render — that's a
    // network test. Just verify the leaflet root is wired up.
    const view = page.locator("#map-view");
    await expect(view).toBeVisible({ timeout: 10000 });

    const leafletRoot = page.locator(".leaflet-container, #map-leaflet");
    await expect(leafletRoot.first()).toBeVisible({ timeout: 15000 });
  });
});
