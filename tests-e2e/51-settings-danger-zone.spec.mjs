// @ts-check
/**
 * UAT Test 12 (M-S4): Danger zone wipe-count + 'type delete' guard.
 *
 * Verifies in a real browser (the only thing the user trusts after the
 * last bug):
 *   1. Settings → Library → Danger zone shows the real photo count,
 *      thousand-separated, plural form correct (e.g. "3,842 photos").
 *   2. Delete-all button is disabled when the confirm input is empty.
 *   3. Typing 'delete' (exact) enables the button.
 *   4. Typing something else (or clearing) disables it again.
 *   5. THE BUTTON IS NEVER CLICKED. The user's demo library must
 *      survive this run intact. A safety check at the end re-reads
 *      /api/v1/photos to assert the count didn't change.
 */
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("UAT 12 — wipe-count + delete guard (read-only)", () => {
  test("danger zone shows real count, guard enables only on exact 'delete'", async ({ page }) => {
    await openApp(page);

    // Snapshot the count BEFORE we touch anything. If the count
    // changes at the end, the test broke the user's library.
    const before = await page.evaluate(async () => {
      const token = /** @type {HTMLMetaElement | null} */ (
        document.querySelector('meta[name="auth-token"]')
      )?.content;
      const r = await fetch("/api/v1/photos?limit=1", {
        headers: { "X-Auth-Token": token || "" },
      });
      const d = await r.json();
      return d.total ?? d.count ?? 0;
    });
    expect(before, "library should be non-empty for this test to mean anything").toBeGreaterThan(0);

    // Open Settings → Library tab
    const gear = page.locator("#btn-settings-toolbar");
    await gear.click();
    const modal = page.locator("#settings-overlay");
    await expect(modal).toHaveClass(/visible/);
    await page.locator('[data-tab="library"]').click();

    // The warning text should mention the real number, comma-separated.
    const warning = page.locator("#clear-photo-count");
    await expect(warning).toBeVisible();
    const formatted = before.toLocaleString();
    await expect(warning).toContainText(formatted, { timeout: 5000 });
    await expect(warning).toContainText(before === 1 ? /\b1\s+photo\b/ : /\bphotos\b/);

    // Button starts disabled (no confirm text).
    const btn = page.locator("#btn-clear-library");
    await expect(btn).toBeVisible();
    const input = page.locator("#clear-confirm-input");
    await input.fill("");
    // Implementation uses an 'enabled' class, not the disabled attr.
    await expect(btn).not.toHaveClass(/enabled/);

    // Wrong text → still disabled. Validator does
    // value.trim().toLowerCase() === "delete", so partial / extra
    // text is rejected but case is forgiving.
    await input.fill("delete me");
    await expect(btn).not.toHaveClass(/enabled/);
    await input.fill("del");
    await expect(btn).not.toHaveClass(/enabled/);

    // Case-insensitive 'delete' → enabled (DELETE / Delete / delete all work).
    await input.fill("DELETE");
    await expect(btn).toHaveClass(/enabled/);
    await input.fill("delete");
    await expect(btn).toHaveClass(/enabled/);
    await input.fill("  Delete  ");
    await expect(btn).toHaveClass(/enabled/);

    // Clearing → disabled again.
    await input.fill("");
    await expect(btn).not.toHaveClass(/enabled/);

    // **DO NOT CLICK btn.** Close the modal cleanly.
    await page.keyboard.press("Escape");
    await expect(modal).not.toHaveClass(/visible/);

    // Re-fetch the count: it MUST be unchanged.
    const after = await page.evaluate(async () => {
      const token = /** @type {HTMLMetaElement | null} */ (
        document.querySelector('meta[name="auth-token"]')
      )?.content;
      const r = await fetch("/api/v1/photos?limit=1", {
        headers: { "X-Auth-Token": token || "" },
      });
      const d = await r.json();
      return d.total ?? d.count ?? 0;
    });
    expect(
      after,
      `library count changed (${before} → ${after}) — UAT 12 spec must be non-destructive`
    ).toBe(before);
  });
});
