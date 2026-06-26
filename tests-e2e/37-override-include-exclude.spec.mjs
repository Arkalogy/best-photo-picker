// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";
import { setOverrideAndReturnRestorer } from "./_mutate_helpers.mjs";

test.describe("37 — Override include/exclude round-trip", () => {
  test("set include override → /api/overrides reflects it → revert", async ({ page }) => {
    await openApp(page);

    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "no photos");
    const filepath = list[0].filepath;
    const originalMode = list[0].override || null;

    let restore = async () => {};
    try {
      restore = await setOverrideAndReturnRestorer(page, filepath, originalMode, "include");

      // Fetch overrides API and confirm our filepath shows up as include
      const overrides = await api(page, "/api/v1/overrides");
      const map = overrides.overrides || overrides;
      // The shape varies — handle both array of {filepath, mode} and
      // dict {filepath: mode}.
      let mode = null;
      if (Array.isArray(map)) {
        mode = (map.find((o) => o.filepath === filepath) || {}).mode || null;
      } else if (map && typeof map === "object") {
        mode = map[filepath] || null;
      }
      expect(mode).toBe("include");
    } finally {
      await restore();
    }
  });

  test("set exclude override → /api/overrides reflects it → revert", async ({ page }) => {
    await openApp(page);

    const photos = await api(page, "/api/v1/photos?limit=1");
    const list = photos.photos || photos;
    test.skip(list.length === 0, "no photos");
    const filepath = list[0].filepath;
    const originalMode = list[0].override || null;

    let restore = async () => {};
    try {
      restore = await setOverrideAndReturnRestorer(page, filepath, originalMode, "exclude");

      const overrides = await api(page, "/api/v1/overrides");
      const map = overrides.overrides || overrides;
      let mode = null;
      if (Array.isArray(map)) {
        mode = (map.find((o) => o.filepath === filepath) || {}).mode || null;
      } else if (map && typeof map === "object") {
        mode = map[filepath] || null;
      }
      expect(mode).toBe("exclude");
    } finally {
      await restore();
    }
  });
});
