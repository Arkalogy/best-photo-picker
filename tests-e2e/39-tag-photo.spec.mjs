// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { tagPhotoAndReturnRestorer } from "./_mutate_helpers.mjs";

test.describe("39 — Tag a photo, verify in API, cleanup deletes tag", () => {
  test("creates a tag, attaches to photo, restorer removes both", async ({ page }) => {
    await openApp(page);

    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "no photos");
    const photo = list[0];
    test.skip(photo.id == null, "photos endpoint did not return id");

    let helper = { restore: async () => {}, tagId: 0, tagName: "" };
    try {
      helper = await tagPhotoAndReturnRestorer(page, photo.id);

      // Photo's tag list now contains the new tag
      const after = await api(page, `/api/v1/photos/${photo.id}/tags`);
      const tags = after.tags || [];
      const found = tags.find((t) => t.id === helper.tagId);
      expect(found).toBeTruthy();
      expect(found.name).toBe(helper.tagName.toLowerCase());

      // The tag also shows up in the global /api/tags list
      const all = await api(page, "/api/v1/tags");
      const allList = all.tags || [];
      expect(allList.find((t) => t.id === helper.tagId)).toBeTruthy();
    } finally {
      await helper.restore();
    }

    // After cleanup the tag is gone from the global list
    const all2 = await api(page, "/api/v1/tags");
    const allList2 = all2.tags || [];
    expect(allList2.find((t) => t.id === helper.tagId)).toBeFalsy();
  });
});
