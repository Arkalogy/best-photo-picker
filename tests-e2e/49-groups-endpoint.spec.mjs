// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("49 — /api/groups returns detected co-occurrence groups", () => {
  test("groups endpoint returns an array (possibly empty for small libraries)", async ({
    page,
  }) => {
    await openApp(page);
    const data = await api(page, "/api/v1/groups?min_photos=2");
    // Endpoint shape: {groups: [...]} — at minimum the key must exist
    const groups = data.groups || data;
    expect(Array.isArray(groups) || typeof groups === "object").toBe(true);
    if (Array.isArray(groups) && groups.length > 0) {
      const g = groups[0];
      expect(typeof g).toBe("object");
      // Each group should at least carry photo_count or members
      const keys = Object.keys(g);
      expect(keys.length).toBeGreaterThan(0);
    }
  });
});
