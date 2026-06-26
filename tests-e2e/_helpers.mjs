// @ts-check
/**
 * Shared helpers for end-to-end Playwright tests.
 *
 * The dev server is expected to be running at http://localhost:5001
 * with a populated library. Tests are read-mostly — they don't import,
 * delete, or rename anything. State-mutating tests (verdicts, deletes)
 * are explicitly opted in with descriptive expectations.
 */

import { expect } from "@playwright/test";

/**
 * Open the app, wait for the photo grid (or empty state) to appear,
 * and tolerate the onboarding overlay that fires on first run.
 *
 * @param {import("@playwright/test").Page} page
 */
export async function openApp(page) {
  // Surface JS errors as test failures
  /** @type {string[]} */
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));

  await page.goto("/");
  // Wait for the toolbar to be visible — main app shell ready
  await expect(page.locator("#toolbar")).toBeVisible({ timeout: 15000 });

  // Dismiss onboarding overlay if it's a first-run / empty library
  const onboarding = page.locator(".onboarding-overlay, #onboarding");
  if (await onboarding.isVisible().catch(() => false)) {
    // Onboarding is informational; skip via Esc or Skip button
    await page.keyboard.press("Escape").catch(() => {});
  }

  // Allow a beat for client JS to finish initializing
  await page.waitForTimeout(500);
  return errors;
}

/**
 * Click an item in the album sidebar by its visible name.
 * @param {import("@playwright/test").Page} page
 * @param {string} name
 */
export async function clickSidebarAlbum(page, name) {
  const sidebar = page.locator(".sidebar");
  await sidebar.locator(".nav-item", { hasText: name }).first().click();
}

/**
 * Resolve the meta-tag auth token (the same value other JS uses).
 * @param {import("@playwright/test").Page} page
 */
export async function authToken(page) {
  return await page.locator('meta[name="auth-token"]').getAttribute("content");
}

/**
 * Hit a JSON API endpoint with the auth token attached.
 * @param {import("@playwright/test").Page} page
 * @param {string} path
 */
export async function api(page, path) {
  const token = await authToken(page);
  const sep = path.includes("?") ? "&" : "?";
  const resp = await page.request.get(`${path}${sep}_token=${token}`);
  expect(resp.ok()).toBeTruthy();
  return await resp.json();
}

/**
 * Read the live library item count off /api/v1/stats. Used by library-
 * specific specs (`@demo`, `@empty`) to self-skip when the runner pointed
 * the server at the wrong fixture — the multi-pass runner targets each
 * spec at its intended library via tag; this is the safety net if it
 * doesn't. Assumes the page has already navigated (the token meta tag
 * must exist).
 *
 * Reads `total_count` (a plain COUNT of non-deleted rows), NOT
 * `photo_count`: the latter is SUM(is_video=0 AND is_raw=0), which
 * collapses to 0 on older libraries where those flags are NULL (the demo
 * library is one — 3,842 photos, photo_count=0). total_count is the
 * reliable "how many items are in this library" figure.
 * @param {import("@playwright/test").Page} page
 * @returns {Promise<number>}
 */
export async function photoCount(page) {
  const stats = await api(page, "/api/v1/stats");
  return Number(stats?.total_count ?? 0);
}
