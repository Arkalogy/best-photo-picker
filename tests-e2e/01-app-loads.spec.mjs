// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("01 — App boots and shell renders", () => {
  test("loads the home page without console errors and shows toolbar + sidebar", async ({
    page,
  }) => {
    const errors = await openApp(page);

    // Core shell elements visible
    await expect(page.locator("#toolbar")).toBeVisible();
    await expect(page.locator(".sidebar")).toBeVisible();

    // Auth-token meta tag is present (otherwise no API call would work)
    const token = await page.locator('meta[name="auth-token"]').getAttribute("content");
    expect(token).toBeTruthy();
    expect(token?.length).toBeGreaterThan(20);

    // No JS pageerror events fired during load
    expect(errors).toEqual([]);
  });
});
