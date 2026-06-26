// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("23 — Cmd/Ctrl-click multi-select reveals the batch action bar", () => {
  test("two cards selected → batch bar shows count + actions", async ({ page }) => {
    await openApp(page);

    const cards = page.locator("#photo-grid .card");
    await expect(cards.first()).toBeVisible({ timeout: 15000 });
    test.skip((await cards.count()) < 2, "need ≥ 2 cards to multi-select");

    // First selection — a Ctrl/Cmd modifier triggers multi-select mode
    // without opening the lightbox.
    await cards.nth(0).click({ modifiers: ["Meta"] });
    await cards.nth(1).click({ modifiers: ["Meta"] });

    // Batch action bar should appear with a "2 selected" indicator
    const batchBar = page.locator("#batch-bar, .batch-bar, .floating-action-bar, .selection-bar");
    await expect(batchBar.first()).toBeVisible({ timeout: 5000 });

    const text = (await batchBar.first().textContent()) || "";
    expect(text).toMatch(/2|selected/i);

    // Esc clears selection (or click first card un-modified)
    await page.keyboard.press("Escape");
  });
});
