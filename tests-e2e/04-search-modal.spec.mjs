// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("04 — Search modal opens and accepts input", () => {
  test("clicking the search toolbar button opens the search overlay", async ({ page }) => {
    await openApp(page);

    // Open via the toolbar button — Cmd+K is intercepted by macOS
    // WKWebView in the Tauri shell, so the button is the reliable path.
    await page.locator("#btn-search-toolbar").click();

    const overlay = page.locator("#search-overlay");
    await expect(overlay).toBeVisible();
    await expect(overlay).toHaveClass(/visible/);

    const input = page.locator("#search-input");
    await expect(input).toBeFocused();

    // VERACITY: a non-empty query against a populated library should
    // produce at least one result row (filename / album / CLIP / face).
    // "IMG" matches the IMG_xxxx.jpg filenames the e2e fixture generates
    // (bpp/demo/generate.py) — and the IMG_ prefix on most real iPhone
    // libraries — so it's deterministic where "photo" matched nothing.
    await input.fill("IMG");
    // Debounce + remote call — give it a beat
    const results = page.locator("#search-results");
    await expect(results).not.toBeEmpty({ timeout: 5000 });

    await page.keyboard.press("Escape");
    await expect(overlay).not.toHaveClass(/visible/);
  });
});
