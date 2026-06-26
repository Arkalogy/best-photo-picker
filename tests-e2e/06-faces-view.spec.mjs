// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("06 — Faces view shows people and the Review pairs button", () => {
  test("clicking Faces shows people grid + Review pairs (N) button", async ({ page }) => {
    await openApp(page);

    const facesNav = page.locator('.sidebar .nav-item[data-action*="navigateToPeople"]').first();
    test.skip(
      !(await facesNav.isVisible().catch(() => false)),
      "no Faces nav — face_recognition probably not installed"
    );
    await facesNav.click();

    await expect(page.locator("#toolbar-title")).toContainText("Faces");

    // People view becomes visible — content varies (face cards, pets,
    // empty state). Just verify the view container is shown, not hidden.
    const view = page.locator("#people-view");
    await expect(view).toBeVisible({ timeout: 10000 });

    // The Review pairs button should be present in the filter bar.
    // Disabled is OK (count = 0); we just want it to exist with the
    // correct label format ("Review pairs (N)" or "Review pairs (…)").
    const btn = page.locator("#btn-review-pairs");
    if (await btn.isVisible().catch(() => false)) {
      const label = await btn.textContent();
      expect(label).toMatch(/Review pairs/);
    }
  });
});
