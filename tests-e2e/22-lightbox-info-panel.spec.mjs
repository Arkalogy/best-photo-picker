// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("22 — Lightbox info panel renders metadata", () => {
  test("opening a photo populates lb-panel-body with score / EXIF content", async ({ page }) => {
    await openApp(page);

    await page.locator("#photo-grid .card").first().click();
    await expect(page.locator("#lightbox")).toHaveClass(/visible/);

    const panel = page.locator("#lb-panel-body");
    // Panel is always rendered (single-panel design — no tabs); content
    // populates as the photo loads.
    await expect(panel).toBeVisible({ timeout: 5000 });
    // Wait for non-empty body content (scores, EXIF, etc render in a
    // single tick after openLightbox)
    await expect(panel).not.toBeEmpty({ timeout: 5000 });

    // Common content tokens — at least one should appear (filename row,
    // score label, "Quality", or any of the SCORE_LABELS values).
    const text = (await panel.textContent()) || "";
    expect(text).toMatch(/(Quality|Sharpness|Exposure|Faces|Composition|MB|KB)/i);

    await page.keyboard.press("Escape");
  });
});
