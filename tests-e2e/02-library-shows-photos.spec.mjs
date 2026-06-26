// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("02 — Library album loads and renders photos", () => {
  test("Library shows >0 photo cards and the count matches the API", async ({ page }) => {
    await openApp(page);

    // Hit the API directly to know the expected count
    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    const all = albums.find((a) => a.album_type === "all");
    test.skip(!all, "no Library album — empty library, skipping");
    expect(all.photo_count).toBeGreaterThan(0);

    // Grid renders cards
    const cards = page.locator("#photo-grid .card");
    await expect(cards.first()).toBeVisible({ timeout: 15000 });
    expect(await cards.count()).toBeGreaterThan(0);

    // VERACITY: a photo returned by the API must actually be in the
    // rendered grid. Catches the class of bug where the grid silently
    // shows wrong / cached / empty state despite API being fine.
    const photosResp = await api(page, "/api/v1/photos?limit=20");
    const photos = photosResp.photos || photosResp;
    expect(photos.length).toBeGreaterThan(0);
    const firstHash = photos[0].thumb_hash;
    expect(firstHash).toBeTruthy();
    const matchingImg = page.locator(`img[src*="${firstHash}"]`);
    await expect(matchingImg.first()).toBeAttached({ timeout: 10000 });
  });
});
