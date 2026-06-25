// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { favoriteAndReturnRestorer } from "./_mutate_helpers.mjs";

test.describe("36 — Favorite toggle round-trip (state mutation + cleanup)", () => {
  test("favorite a photo, verify in API, then unfavorite to restore", async ({ page }) => {
    await openApp(page);

    // Pick the first photo from the API
    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "library has no photos");
    const target = list[0];
    const filepath = target.filepath;

    // Snapshot original favorite state
    const original = !!target.favorite;

    let restore = async () => {};
    try {
      restore = await favoriteAndReturnRestorer(page, filepath);

      // Verify the toggle actually changed state — fetch the photo back
      const after = await api(
        page,
        `/api/v1/albums/${
          (await api(page, "/api/v1/albums")).albums.find((a) => a.album_type === "all").id
        }/photos?limit=2000`
      );
      const photo = (after.photos || after).find((p) => p.filepath === filepath);
      expect(photo).toBeTruthy();
      expect(!!photo.favorite).toBe(!original);
    } finally {
      await restore();
    }
  });
});
