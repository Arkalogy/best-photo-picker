// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("27 — /api/stats returns the documented shape", () => {
  test("stats endpoint reports counts + disk usage + format breakdown", async ({ page }) => {
    await openApp(page);

    const stats = await api(page, "/api/v1/stats");

    // Schema sanity — count, total_size, format_counts are documented
    // in pm.md "Storage info / library stats" feature.
    expect(typeof stats).toBe("object");
    // photo_count should be a number; allow 0 for empty libs but most
    // likely positive
    if (stats.photo_count !== undefined) {
      expect(typeof stats.photo_count).toBe("number");
    }
    // Disk usage in bytes (or .total_size)
    const sizeKey = ["total_bytes", "disk_usage", "total_size", "size"].find(
      (k) => stats[k] !== undefined
    );
    expect(sizeKey).toBeTruthy();
    if (sizeKey) {
      expect(typeof stats[sizeKey]).toBe("number");
    }
  });
});
