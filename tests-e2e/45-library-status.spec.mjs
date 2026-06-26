// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("45 — /api/library/status reports library_path + batches", () => {
  test("library status returns expected shape and exists=true for local library", async ({
    page,
  }) => {
    await openApp(page);
    const status = await api(page, "/api/v1/library/status");
    expect(typeof status.library_path).toBe("string");
    expect(status.library_path.length).toBeGreaterThan(0);
    expect(status.exists).toBe(true);
    expect(Array.isArray(status.batches)).toBe(true);
    expect(typeof status.importing).toBe("boolean");
  });
});
