// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { deleteApi, postApi } from "./_mutate_helpers.mjs";
import { authToken } from "./_helpers.mjs";

test.describe("47 — Tag CRUD: create + rename + delete chain", () => {
  test("creates a tag, renames it, then deletes it — verified at each step", async ({ page }) => {
    await openApp(page);
    const original = `__e2e_crud_${Date.now()}`;
    const renamed = `__e2e_crud_renamed_${Date.now()}`;

    let tagId = 0;
    try {
      // CREATE
      const r1 = await postApi(page, "/api/v1/tags", { name: original });
      expect(r1.ok()).toBe(true);
      const c = await r1.json();
      tagId = c.id;
      expect(c.name).toBe(original.toLowerCase());

      // List shows it
      const list1 = await api(page, "/api/v1/tags");
      const found1 = (list1.tags || []).find((t) => t.id === tagId);
      expect(found1).toBeTruthy();
      expect(found1.name).toBe(original.toLowerCase());

      // RENAME (PUT)
      const token = await authToken(page);
      const r2 = await page.request.put(`/api/v1/tags/${tagId}?_token=${token}`, {
        data: { name: renamed },
        headers: { "Content-Type": "application/json" },
      });
      expect(r2.ok()).toBe(true);
      const u = await r2.json();
      expect(u.name).toBe(renamed.toLowerCase());

      // List reflects rename
      const list2 = await api(page, "/api/v1/tags");
      const found2 = (list2.tags || []).find((t) => t.id === tagId);
      expect(found2.name).toBe(renamed.toLowerCase());
    } finally {
      // DELETE (cleanup, also exercises the delete path)
      if (tagId) await deleteApi(page, `/api/v1/tags/${tagId}`);
    }

    // Confirm gone
    const list3 = await api(page, "/api/v1/tags");
    expect((list3.tags || []).find((t) => t.id === tagId)).toBeFalsy();
  });
});
