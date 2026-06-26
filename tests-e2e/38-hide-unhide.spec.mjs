// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { hideAndReturnRestorer } from "./_mutate_helpers.mjs";

test.describe("38 — Hide/unhide round-trip + Hidden listing reflects state", () => {
  test("hide a photo, see it in /api/photos/hidden, unhide to revert", async ({ page }) => {
    await openApp(page);

    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "no photos");
    const filepath = list[0].filepath;

    let restore = async () => {};
    try {
      restore = await hideAndReturnRestorer(page, filepath);

      // /api/photos/hidden queries the DB directly (no smart album refresh
      // needed) so it's the authoritative signal that hide took effect.
      const hidden = await api(page, "/api/v1/photos/hidden");
      const hiddenList = Array.isArray(hidden) ? hidden : hidden.photos || [];
      const found = hiddenList.find((p) => p.filepath === filepath);
      expect(found).toBeTruthy();
    } finally {
      await restore();
    }

    // Confirm the unhide cleared it.
    const after = await api(page, "/api/v1/photos/hidden");
    const afterList = Array.isArray(after) ? after : after.photos || [];
    expect(afterList.find((p) => p.filepath === filepath)).toBeFalsy();
  });
});
