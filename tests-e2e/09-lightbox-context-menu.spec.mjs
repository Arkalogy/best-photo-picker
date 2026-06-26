// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("09 — Lightbox right-click context menu shows the standard items", () => {
  test("right-click in lightbox reveals Include/Exclude/Favorite/Edit/Enhance/Show/Delete", async ({
    page,
  }) => {
    await openApp(page);

    const firstCard = page.locator("#photo-grid .card").first();
    await expect(firstCard).toBeVisible({ timeout: 15000 });
    await firstCard.click();

    const lb = page.locator("#lightbox");
    await expect(lb).toHaveClass(/visible/);

    // Right-click on the .lb-img-wrapper — that's where the
    // oncontextmenu handler lives (see templates/index.html).
    // Position matters: when face overlays are present they intercept
    // the right-click (their own data-oncontextmenu wins via closest()).
    // Click at the bottom-right corner, far from any reasonable face
    // bbox, so we always hit the wrapper's handler.
    const wrapper = page.locator(".lb-img-wrapper").first();
    const wrapperBox = await wrapper.boundingBox();
    if (!wrapperBox) throw new Error("lb-img-wrapper has no bounding box");
    await wrapper.click({
      button: "right",
      position: { x: wrapperBox.width - 50, y: wrapperBox.height - 50 },
    });

    const menu = page.locator("#lb-ctx-menu");
    await expect(menu).toBeVisible();
    await expect(menu).not.toHaveClass(/hidden/);

    // Required menu items (Tag person is conditional on detected faces;
    // it'll either be visible with text or hidden via display:none).
    for (const item of [
      "Include",
      "Exclude",
      "Favorite",
      "Edit",
      "Enhance",
      "Show in Finder",
      "Delete",
    ]) {
      await expect(menu).toContainText(item);
    }

    // VERACITY: clicking outside the menu (anywhere on the doc) must
    // dismiss it — global click handler is registered in lightbox.js's
    // initLbCtxMenu IIFE. If that wiring breaks, the menu stays
    // sticky and other clicks miss their targets.
    await page.mouse.click(10, 10);
    await expect(menu).toHaveClass(/hidden/);
  });
});
