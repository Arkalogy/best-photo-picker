// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { postApi } from "./_mutate_helpers.mjs";

test.describe("50 — POST /api/albums/<id>/recompute on the All album returns ok", () => {
  test("recompute on the 'all' album succeeds (idempotent on selection state)", async ({
    page,
  }) => {
    await openApp(page);
    const albumsResp = await api(page, "/api/v1/albums");
    const albums = Array.isArray(albumsResp) ? albumsResp : albumsResp.albums || [];
    const all = albums.find((a) => a.album_type === "all");
    test.skip(!all, "no 'all' album");

    const r = await postApi(page, `/api/v1/albums/${all.id}/recompute`, {});
    // Either 200 with a payload describing the recompute, or 404 if there are
    // truly zero photos. Both are acceptable signals — we just want no 500.
    expect([200, 404]).toContain(r.status());
  });
});
