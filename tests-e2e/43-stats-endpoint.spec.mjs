// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("43 — /api/stats returns library-wide counts and breakdown", () => {
  test("stats response has total_count + format_breakdown shape", async ({ page }) => {
    await openApp(page);
    const stats = await api(page, "/api/v1/stats");
    expect(typeof stats.total_count).toBe("number");
    expect(typeof stats.total_size).toBe("number");
    expect(typeof stats.photo_count).toBe("number");
    expect(typeof stats.video_count).toBe("number");
    expect(typeof stats.raw_count).toBe("number");
    expect(typeof stats.format_breakdown).toBe("object");
    // total_count should be the sum (best-effort sanity, library may have edge types)
    expect(stats.total_count).toBeGreaterThanOrEqual(stats.video_count);
    expect(stats.total_count).toBeGreaterThanOrEqual(stats.raw_count);
  });
});
