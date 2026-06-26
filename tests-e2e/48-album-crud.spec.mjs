// @ts-check
import { expect, test } from "@playwright/test";

import { api, authToken, openApp } from "./_helpers.mjs";
import { deleteApi, postApi } from "./_mutate_helpers.mjs";

test.describe("48 — Manual Album CRUD: create + update name + delete", () => {
  test("creates a manual album, updates its name, then deletes it", async ({ page }) => {
    await openApp(page);
    const original = `__e2e_album_${Date.now()}`;
    const renamed = `${original}_renamed`;

    let albumId = 0;
    try {
      // CREATE
      const r1 = await postApi(page, "/api/v1/albums", { name: original });
      expect(r1.ok()).toBe(true);
      const c = await r1.json();
      albumId = c.id;
      expect(typeof albumId).toBe("number");

      // GET — verify created
      const a1 = await api(page, `/api/v1/albums/${albumId}`);
      expect(a1.album.name).toBe(original);
      expect(a1.album.album_type).toBe("manual");

      // UPDATE (PUT) — rename
      const token = await authToken(page);
      const r2 = await page.request.put(`/api/v1/albums/${albumId}?_token=${token}`, {
        data: { name: renamed },
        headers: { "Content-Type": "application/json" },
      });
      expect(r2.ok()).toBe(true);

      const a2 = await api(page, `/api/v1/albums/${albumId}`);
      expect(a2.album.name).toBe(renamed);
    } finally {
      if (albumId) await deleteApi(page, `/api/v1/albums/${albumId}`);
    }

    // Confirm 404 after delete
    const token = await authToken(page);
    const resp = await page.request.get(`/api/v1/albums/${albumId}?_token=${token}`);
    expect(resp.status()).toBe(404);
  });
});
