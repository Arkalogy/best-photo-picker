// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("25 — Toolbar surfaces all primary action buttons", () => {
  test("search, analyze, sort, filter, slideshow, import, export, settings all visible", async ({
    page,
  }) => {
    await openApp(page);

    // Each toolbar button is documented in templates/index.html with a
    // stable id. Verifying their presence catches header regressions
    // (e.g. accidentally removing an export button during a refactor).
    const ids = [
      "btn-search-toolbar",
      "btn-analyze-toolbar",
      "btn-sort",
      "btn-filter",
      "btn-slideshow",
      "btn-import-toolbar",
      "btn-export",
      "btn-settings-toolbar",
    ];
    for (const id of ids) {
      const btn = page.locator(`#${id}`);
      await expect(btn, `#${id} should exist in the toolbar`).toBeAttached();
    }
  });
});
