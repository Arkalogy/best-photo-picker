// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("08 — Duplicates album + Review Duplicates button", () => {
  test("Duplicates album in sidebar → toolbar shows Review Duplicates", async ({ page }) => {
    await openApp(page);

    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    const dupes = albums.find((a) => a.album_type === "smart_duplicates");
    test.skip(!dupes, "no Duplicates album — no phash duplicates in library");

    // nav-item has its name + photo count in the same text node, so
    // anchor on the data-album-id from the API instead.
    await page.locator(`.sidebar .nav-item[data-album-id="${dupes.id}"]`).first().click();

    await expect(page.locator("#toolbar-title")).toContainText("Duplicates", {
      timeout: 10000,
    });

    // The Review Duplicates button is dynamically created in the
    // toolbar-right area when we land on the Duplicates album.
    const reviewBtn = page.locator("#btn-review-dupes");
    await expect(reviewBtn).toBeVisible({ timeout: 5000 });
    await expect(reviewBtn).toContainText(/Review Duplicates/);

    // Sanity: the dupe-groups API reports ≥ 1 group
    const groupData = await api(page, "/api/v1/duplicates/groups");
    expect((groupData.groups || []).length).toBeGreaterThan(0);

    // VERACITY: clicking Review Duplicates must open the compare
    // overlay with "Best Photo" / "Duplicate" labels (v2 of the
    // dupe-review flow uses that compare-view rebrand).
    await reviewBtn.click();
    const compare = page.locator("#compare-overlay");
    await expect(compare).toBeVisible({ timeout: 5000 });
    await expect(compare).toHaveClass(/visible/);
    await expect(compare).toContainText("Best Photo");
    await expect(compare).toContainText("Duplicate");
    // Group counter present
    await expect(compare).toContainText(/Group \d+ of \d+/);

    // Clean up — Esc should bail without recording any verdict
    await page.keyboard.press("Escape");
    await expect(compare).not.toHaveClass(/visible/);
  });
});
