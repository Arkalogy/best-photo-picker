// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { manualAlbumWithPhotoAndReturnRestorer } from "./_mutate_helpers.mjs";

test.describe("40 — Manual album: create + add photo + cleanup", () => {
  test("create album with one photo, verify, then delete to revert", async ({ page }) => {
    await openApp(page);

    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "no photos");
    const filepath = list[0].filepath;

    let helper = { restore: async () => {}, albumId: 0, name: "" };
    try {
      helper = await manualAlbumWithPhotoAndReturnRestorer(page, filepath);

      // Album appears in /api/albums and contains the photo
      const albums = await api(page, "/api/v1/albums");
      const albumsList = Array.isArray(albums) ? albums : albums.albums || [];
      const album = albumsList.find((a) => a.id === helper.albumId);
      expect(album).toBeTruthy();
      expect(album.name).toBe(helper.name);
      // Manual album type is "manual"
      expect(album.album_type).toBe("manual");

      // The album's photo list contains our photo
      const albumPhotos = await api(page, `/api/v1/albums/${helper.albumId}/photos?limit=10`);
      const phs = albumPhotos.photos || [];
      expect(phs.find((p) => p.filepath === filepath)).toBeTruthy();
    } finally {
      await helper.restore();
    }

    // Album is gone after cleanup
    const after = await api(page, "/api/v1/albums");
    const afterList = Array.isArray(after) ? after : after.albums || [];
    expect(afterList.find((a) => a.id === helper.albumId)).toBeFalsy();
  });
});
