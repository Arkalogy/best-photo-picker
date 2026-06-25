// @ts-check
/**
 * UAT Test 16: BPP Picks recompute via the K input.
 *
 * Path:
 *   user types a new K in #toolbar-k → syncKFromToolbar mirrors it into
 *   #param-k → scheduleRecompute debounces 150ms → doRecompute fires
 *   → server returns a new selected_paths set → grid shows N picks.
 *
 * Real-browser test on the demo library (~3,842 photos). Switches the
 * filter to BPP Picks first so the count we read off the toolbar /
 * selected-paths is what actually changes when K is moved. Restores
 * K to 50 at the end. Non-destructive.
 */
import { expect, test } from "@playwright/test";

import { openApp, photoCount } from "./_helpers.mjs";

// `@demo` — local-only. Needs the real demo library (~3,842 photos) for
// the K=25/K=50 recompute counts to be meaningful. Not run in CI (no demo
// library there). Run before releases via `npm run test:e2e:demo`. Against
// a small synthetic/empty library this self-skips (see the guard below).
test.describe("UAT 16 @demo — toolbar K change triggers recompute, picks count follows", () => {
  test("K=25 picks 25, K=50 picks 50, no errors during debounce", async ({ page }) => {
    /** @type {string[]} */
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await openApp(page);
    test.skip(
      (await photoCount(page)) < 50,
      "needs the demo library with ≥50 photos (run via test:e2e:demo)"
    );

    const toolbarK = page.locator("#toolbar-k");
    await expect(toolbarK).toBeVisible();

    // Initial app load doesn't always populate selectedPaths — the
    // recompute is lazy. Fire it explicitly so we have a known starting
    // point. Wait until it lands.
    await page.evaluate(() => {
      const w = /** @type {any} */ (window);
      if (typeof w.doRecompute === "function") return w.doRecompute({});
    });
    await expect
      .poll(
        async () =>
          page.evaluate(() => {
            const w = /** @type {any} */ (window);
            return (w.selectedPaths instanceof Set ? w.selectedPaths.size : 0) || 0;
          }),
        { timeout: 15000, intervals: [200, 400, 800] }
      )
      .toBeGreaterThan(0);

    // Bump K to a small, distinctive value. fill() + dispatch input
    // because scheduleRecompute is bound via data-oninput dispatch on
    // #param-k; for the toolbar entry the bound event is 'change' via
    // data-onchange="syncKFromToolbar". Use blur to trigger 'change'.
    await toolbarK.click();
    await toolbarK.fill("25");
    await toolbarK.press("Tab"); // blur → change event → syncKFromToolbar

    // Wait for the recompute. Poll the size — first the debounce (150ms),
    // then network round-trip on a 3.8k-photo recompute.
    await expect
      .poll(
        async () =>
          page.evaluate(() => {
            const w = /** @type {any} */ (window);
            return (w.selectedPaths instanceof Set ? w.selectedPaths.size : 0) || 0;
          }),
        { timeout: 15000, intervals: [200, 400, 800] }
      )
      .toBe(25);

    // Restore K=50 so the demo lib state is left as we found it.
    await toolbarK.click();
    await toolbarK.fill("50");
    await toolbarK.press("Tab");
    await expect
      .poll(
        async () =>
          page.evaluate(() => {
            const w = /** @type {any} */ (window);
            return (w.selectedPaths instanceof Set ? w.selectedPaths.size : 0) || 0;
          }),
        { timeout: 15000, intervals: [200, 400, 800] }
      )
      .toBe(50);

    const noisy = errors.filter(
      (e) => !/ResizeObserver loop/i.test(e) && !/^Script error\.?$/i.test(e)
    );
    expect(noisy, `unexpected errors: ${noisy.join(" | ")}`).toEqual([]);
  });
});
