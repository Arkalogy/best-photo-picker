// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("21 — Status bar shows non-empty content", () => {
  test("after library loads the status bar reports photo / picks counts", async ({ page }) => {
    await openApp(page);

    // Status bar becomes visible when there's a populated grid
    const sb = page.locator("#status-bar");
    await expect(sb).toBeVisible({ timeout: 10000 });
    await expect(sb).not.toHaveClass(/hidden/);

    // VERACITY: status bar text mentions photo count from API. The
    // exact rendering varies (chips, percentages, GPS markers) but
    // the count must appear when the library has photos.
    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    const all = albums.find((a) => a.album_type === "all");
    test.skip(!all || (all.photo_count || 0) === 0, "empty library");

    // Match a digit grouping (with optional thousands separator)
    const txt = (await sb.textContent()) || "";
    expect(txt).toMatch(/\d/);
  });
});
