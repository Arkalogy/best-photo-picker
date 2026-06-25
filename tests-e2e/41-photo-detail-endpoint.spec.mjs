// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("41 — /api/photos/<id> returns full photo dict", () => {
  test("photo detail endpoint returns the same id and a thumb_hash", async ({ page }) => {
    await openApp(page);
    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "no photos");
    const id = list[0].id;
    test.skip(id == null, "photos endpoint did not return id");

    const detail = await api(page, `/api/v1/photos/${id}`);
    expect(detail.id).toBe(id);
    expect(typeof detail.filepath).toBe("string");
    expect(detail.thumb_hash).toBeTruthy();
  });
});
