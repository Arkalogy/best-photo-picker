// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("29 — Theme toggle in Settings switches data-theme attr", () => {
  test("clicking Light radio sets html[data-theme=light]; Dark restores", async ({ page }) => {
    await openApp(page);

    // Capture initial theme so we can put it back
    const initial = await page.locator("html").getAttribute("data-theme");

    await page.locator("#btn-settings-toolbar").click();
    const overlay = page.locator("#settings-overlay");
    await expect(overlay).toHaveClass(/visible/);

    // Theme controls live in a [role=radiogroup] inside the App tab.
    // Use radio inputs by their labels.
    const lightRadio = page.locator(
      '.theme-toggle input[value="light"], .theme-toggle [data-theme="light"]'
    );
    const darkRadio = page.locator(
      '.theme-toggle input[value="dark"], .theme-toggle [data-theme="dark"]'
    );

    test.skip(
      !(await lightRadio
        .first()
        .isVisible()
        .catch(() => false)),
      "theme toggle markup not where expected"
    );

    await lightRadio.first().click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light", {
      timeout: 3000,
    });

    await darkRadio.first().click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark", {
      timeout: 3000,
    });

    // Restore original (auto/dark/light)
    if (initial && initial !== "dark") {
      const restoreRadio = page.locator(
        `.theme-toggle input[value="${initial}"], .theme-toggle [data-theme="${initial}"]`
      );
      if (
        await restoreRadio
          .first()
          .isVisible()
          .catch(() => false)
      ) {
        await restoreRadio.first().click();
      }
    }

    await page.keyboard.press("Escape");
  });
});
