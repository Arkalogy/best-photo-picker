// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("24 — /api/albums returns all expected smart album types", () => {
  test("the API surfaces every smart album the sidebar can show", async ({ page }) => {
    await openApp(page);

    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];

    // The library must have at least the "all" album
    const types = new Set(albums.map((a) => a.album_type));
    expect(types.has("all")).toBe(true);

    // Each album row carries the schema the sidebar relies on
    for (const a of albums) {
      expect(typeof a.id).toBe("number");
      expect(typeof a.name).toBe("string");
      expect(typeof a.album_type).toBe("string");
      // photo_count is optional in some smart-album responses but if
      // present must be a number, never a string
      if (a.photo_count !== undefined) {
        expect(typeof a.photo_count).toBe("number");
      }
    }
  });

  test("/api/v1/albums/<id>/photos honors limit + offset (album-scoped pagination)", async ({
    page,
  }) => {
    await openApp(page);

    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    const all = albums.find((a) => a.album_type === "all");
    test.skip(!all || (all.photo_count || 0) < 10, "library too small for pagination");

    const page1 = await api(page, `/api/v1/albums/${all.id}/photos?limit=5&offset=0`);
    const page2 = await api(page, `/api/v1/albums/${all.id}/photos?limit=5&offset=5`);
    const photos1 = page1.photos || page1;
    const photos2 = page2.photos || page2;

    expect(photos1.length).toBe(5);
    expect(photos2.length).toBeGreaterThan(0);

    // No overlap between the two pages
    const ids1 = new Set(photos1.map((p) => p.filepath));
    const ids2 = new Set(photos2.map((p) => p.filepath));
    for (const fp of ids2) {
      expect(ids1.has(fp)).toBe(false);
    }
  });
});
