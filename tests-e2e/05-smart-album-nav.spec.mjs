// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("05 — Smart album navigation filters the grid", () => {
  test("clicking any smart album switches view and reloads the grid", async ({ page }) => {
    await openApp(page);

    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    // Pick the first non-"all" smart album that has photos
    const target = albums.find(
      (a) => a.album_type && a.album_type.startsWith("smart_") && (a.photo_count || 0) > 0
    );
    test.skip(!target, "no populated smart albums — empty library, skipping");

    await page.locator(`.sidebar .nav-item[data-album-id="${target.id}"]`).first().click();

    // Toolbar title updates to the album name
    await expect(page.locator("#toolbar-title")).toContainText(target.name, {
      timeout: 5000,
    });

    // VERACITY: the toolbar subtitle should reflect the album's photo
    // count from the API. Catches "switched header but didn't reload
    // grid" regressions.
    //
    // photos.mjs renders the subtitle as "<N> selected of <M>" where M
    // is the active-photo count (== album photo_count for a smart album
    // with no active filter). Match the trailing `of <M>` so the regex
    // tracks the actual production format.
    const subtitle = page.locator("#toolbar-subtitle");
    const expectedCount = target.photo_count || 0;
    await expect
      .poll(async () => (await subtitle.textContent()) || "", { timeout: 5000 })
      .toMatch(new RegExp(`of\\s+${expectedCount}\\b`));

    // Grid (or empty state) renders
    const cardsOrEmpty = page.locator("#photo-grid .card, #photo-grid .empty-state");
    await expect(cardsOrEmpty.first()).toBeVisible({ timeout: 10000 });
  });
});
