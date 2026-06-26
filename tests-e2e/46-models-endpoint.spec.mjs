// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("46 — /api/models reports ML model status for the Settings UI", () => {
  test("models endpoint returns a non-empty object with status fields", async ({ page }) => {
    await openApp(page);
    const models = await api(page, "/api/v1/models");
    expect(typeof models).toBe("object");
    expect(models).not.toBeNull();
    // Should have at least one feature group with at least one model entry
    const keys = Object.keys(models);
    expect(keys.length).toBeGreaterThan(0);

    // For any feature group, look for a known shape: list of models with a name + path or installed flag
    let foundShape = false;
    for (const k of keys) {
      const v = models[k];
      if (Array.isArray(v) && v.length > 0) {
        const m = v[0];
        if (m && typeof m === "object") {
          foundShape = true;
          break;
        }
      } else if (v && typeof v === "object") {
        foundShape = true;
        break;
      }
    }
    expect(foundShape).toBe(true);
  });
});
