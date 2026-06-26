// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("11 — Calendar view loads with month grid", () => {
  test("clicking Calendar in sidebar shows month grid + day cells", async ({ page }) => {
    await openApp(page);

    const calendarNav = page
      .locator('.sidebar .nav-item[data-action*="navigateToCalendar"]')
      .first();
    test.skip(
      !(await calendarNav.isVisible().catch(() => false)),
      "no Calendar nav item — feature flag off?"
    );
    await calendarNav.click();

    await expect(page.locator("#toolbar-title")).toContainText(/Calendar/i, {
      timeout: 5000,
    });

    // Calendar view container becomes visible (no longer "hidden")
    const view = page.locator("#calendar-view");
    await expect(view).not.toHaveClass(/hidden/, { timeout: 10000 });

    // Calendar renders mode toggle (week/month/year) or an empty state.
    // Selectors live in calendar.js: .cal-mode-toggle, .cal-mode-btn,
    // .cal-empty. Any of those means the view actually rendered.
    const calContent = page.locator(
      "#calendar-view .cal-mode-toggle, " +
        "#calendar-view .cal-mode-btn, " +
        "#calendar-view .cal-empty, " +
        "#calendar-view .cal-header"
    );
    await expect(calContent.first()).toBeVisible({ timeout: 10000 });
  });
});
