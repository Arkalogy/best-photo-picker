// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("15 — Toolbar Pick (k) input accepts and persists value", () => {
  test("changing the Pick number field reflects in the input value", async ({ page }) => {
    await openApp(page);

    // Pick control is hidden in library view (only inside albums).
    // Drive it via the toolbar k input directly when present.
    const pickInput = page.locator("#toolbar-k");
    if (!(await pickInput.isVisible().catch(() => false))) {
      // Switch into a smart album so the Pick control unhides
      const smartNav = page.locator(".sidebar .nav-item[data-album-id]").first();
      test.skip(
        !(await smartNav.isVisible().catch(() => false)),
        "no album in sidebar to drive Pick scope"
      );
      await smartNav.click();
      await expect(pickInput).toBeVisible({ timeout: 5000 });
    }

    // Capture the current value (will be the album's k or the global k)
    await pickInput.fill("42");
    await pickInput.press("Tab"); // commit
    await expect(pickInput).toHaveValue("42");
  });
});
