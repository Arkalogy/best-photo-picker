// @ts-check
/**
 * UAT Test 14: sidebar tabs view-guard.
 *
 * Regression: late-arriving fetches used to paint stale data into a
 * view the user had already left. The view-guard (view-guard.mjs)
 * wraps fetches with an AbortController that fires whenever
 * window.currentView changes, and viewFetch returns null when the
 * response arrives after a view switch.
 *
 * This spec exercises the practical UX: click sidebar tabs fast, end
 * on Library, assert the library grid is visible and the calendar
 * view container is hidden (the loser of the race must NOT linger),
 * and no page-level errors fire from aborted fetches.
 */
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

test.describe("UAT 14 — sidebar tab view-guard prevents stale paint", () => {
  test("rapid sidebar clicks settle on the last-clicked view cleanly", async ({ page }) => {
    /** @type {string[]} */
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await openApp(page);

    const calendarNav = page
      .locator('.sidebar .nav-item[data-action*="navigateToCalendar"]')
      .first();
    const favoritesNav = page
      .locator('.sidebar .nav-item[data-action*="navigateToFavorites"]')
      .first();
    test.skip(
      !(await calendarNav.isVisible().catch(() => false)),
      "no Calendar nav item — feature flag off?"
    );
    test.skip(
      !(await favoritesNav.isVisible().catch(() => false)),
      "no Favorites nav item — feature flag off?"
    );

    // Click calendar then immediately favorites — race window where the
    // calendar /api/v1/calendar/months fetch is mid-flight. Without
    // the view-guard, the calendar response would resolve after the
    // currentView=='favorites' write and paint stale month cells into
    // the now-active favorites view.
    await calendarNav.click();
    await favoritesNav.click();

    // The view-guard bumps its internal token on every currentView
    // change. The final value must be 'favorites' — proving the second
    // click won the race AND the first click's late fetch didn't
    // overwrite it. (Favorites reuses the library grid + filter, so
    // toolbar title stays 'Library'; the identity lives in
    // window.currentView.)
    await expect
      .poll(async () => page.evaluate(() => /** @type {any} */ (window).currentView || ""), {
        timeout: 5000,
      })
      .toBe("favorites");

    // Calendar view container must be hidden — if a late calendar fetch
    // unhid it, this would fail.
    const calView = page.locator("#calendar-view");
    await expect(calView).toHaveClass(/hidden/, { timeout: 5000 });

    // Wait long enough for any in-flight aborted fetch to fully settle.
    await page.waitForTimeout(1000);

    // Re-check currentView — late-resolving calendar promise must NOT
    // have stomped it back to 'calendar'.
    const settled = await page.evaluate(() => /** @type {any} */ (window).currentView || "");
    expect(settled, "late calendar fetch must not stomp currentView after settle").toBe(
      "favorites"
    );

    // No page errors from aborts. AbortError must be swallowed by
    // viewFetch; only filter the standard browser noise the app
    // already classifies as benign.
    const noisy = errors.filter(
      (e) => !/ResizeObserver loop/i.test(e) && !/^Script error\.?$/i.test(e)
    );
    expect(noisy, `unexpected errors: ${noisy.join(" | ")}`).toEqual([]);
  });
});
