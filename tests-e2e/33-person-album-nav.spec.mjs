// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("33 — Clicking a person in Faces opens that person's album", () => {
  test("first face card → toolbar title shows the person's name", async ({ page }) => {
    await openApp(page);

    // Find a smart_person album that has at least one photo
    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    const personAlbum = albums.find(
      (a) => a.album_type === "smart_person" && (a.photo_count || 0) > 0
    );
    test.skip(!personAlbum, "no smart_person album with photos");

    // Person albums live inside a <details> Faces folder that may be
    // collapsed. Expand it first so the nav-item becomes visible.
    const facesFolder = page.locator(".sidebar details").filter({ hasText: "Faces" });
    if (
      await facesFolder
        .first()
        .isVisible()
        .catch(() => false)
    ) {
      const isOpen = await facesFolder.first().getAttribute("open");
      if (isOpen === null) {
        await facesFolder.first().locator("summary").click();
      }
    }

    await page.locator(`.sidebar .nav-item[data-album-id="${personAlbum.id}"]`).first().click();

    await expect(page.locator("#toolbar-title")).toContainText(personAlbum.name, {
      timeout: 5000,
    });

    // Person album should reveal the person album action bar
    const bar = page.locator("#person-album-bar");
    await expect(bar).toBeVisible({ timeout: 5000 });
  });
});
