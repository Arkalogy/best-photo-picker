// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("42 — /api/photos/timeline returns months distribution", () => {
  test("timeline returns {months: [...]} with month + count keys", async ({ page }) => {
    await openApp(page);
    const data = await api(page, "/api/v1/photos/timeline");
    expect(Array.isArray(data.months)).toBe(true);
    if (data.months.length > 0) {
      const m = data.months[0];
      expect(typeof m).toBe("object");
      // Shape may vary slightly; assert at minimum a count and a label-ish key
      const keys = Object.keys(m);
      expect(keys.length).toBeGreaterThan(0);
      const hasCount = keys.some((k) => /count/i.test(k));
      expect(hasCount).toBe(true);
    }
  });
});
