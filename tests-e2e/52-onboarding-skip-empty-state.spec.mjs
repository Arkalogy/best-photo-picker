// @ts-check
/**
 * UAT Test 13 (M3): on first run, the onboarding modal appears, the
 * 'Skip — I'll import later' button on step 3 dismisses onboarding,
 * and the welcome empty-state panel (#empty-state) becomes visible.
 *
 * Without the M3 fix this branch left the user on a blank grid with no
 * actionable text — looked broken. The fix in onboarding.mjs:242
 * calls win.showEmptyLibrary() on the skip path.
 *
 * Requires the server to be pointed at an EMPTY library (image_count
 * = 0, first_run = true). The UAT runner spins up a tmp lib before
 * dispatching this spec; the demo library is restored after.
 */
import { expect, test } from "@playwright/test";

import { photoCount } from "./_helpers.mjs";

// `@empty` — the multi-pass runner (scripts/run_e2e.sh empty) points the
// server at a brand-new empty library so onboarding fires on first_run.
// Run via `npm run test:e2e:empty`. Against a populated library this whole
// describe self-skips (see the photoCount guard in each test).
test.describe("UAT 13 @empty — onboarding skip reveals empty-state", () => {
  test("step 3 'Skip' hides onboarding and shows the welcome empty panel", async ({ page }) => {
    /** @type {string[]} */
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto("/");
    test.skip(
      (await photoCount(page)) > 0,
      "needs an empty first-run library (run via test:e2e:empty)"
    );

    // Onboarding should auto-fire on first run.
    const onboarding = page.locator("#onboarding-overlay");
    await expect(onboarding, "onboarding overlay must appear on first_run").not.toHaveClass(
      /hidden/,
      { timeout: 10000 }
    );

    // Advance through the lead-in steps to the final "Ready" step.
    // The wizard is 4 steps (Welcome → Library → Use-context → Ready),
    // each driving _onboardingNext; the overlay tracks the live index in
    // data-step, so click Continue until we land on step 3. Use-context
    // defaults to "personal" pre-selected, so no choice is required.
    for (const target of [1, 2, 3]) {
      await page.locator('[data-action="_onboardingNext"]').first().click();
      await expect(onboarding).toHaveAttribute("data-step", String(target), {
        timeout: 5000,
      });
    }

    // Step 3 (Ready): import-now or skip. We want the skip branch.
    const skip = page.locator('[data-action="_onboardingFinish"][data-arg0="false"]');
    await expect(skip).toBeVisible({ timeout: 5000 });
    await skip.click();

    // Onboarding hidden, empty-state shown.
    await expect(onboarding).toHaveClass(/hidden/, { timeout: 5000 });
    const emptyState = page.locator("#empty-state");
    await expect(emptyState, "empty-state must be revealed after Skip").not.toHaveClass(/hidden/);
    // Must contain actionable copy + the import CTA — not a bare panel.
    await expect(emptyState).toContainText(/import/i);

    expect(errors, `unexpected page errors: ${errors.join(" | ")}`).toEqual([]);
  });

  test("Esc at any onboarding step dismisses to the same empty-state", async ({ page }) => {
    /** @type {string[]} */
    const errors = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto("/");
    test.skip(
      (await photoCount(page)) > 0,
      "needs an empty first-run library (run via test:e2e:empty)"
    );

    const onboarding = page.locator("#onboarding-overlay");
    await expect(onboarding, "onboarding overlay must appear on first_run").not.toHaveClass(
      /hidden/,
      { timeout: 10000 }
    );

    // Press Esc on step 1 (Welcome) — should NOT trap the user.
    // Reflex Esc on a first-run modal is the worst UX trap to leave in.
    await page.keyboard.press("Escape");

    // Onboarding hides, empty-state appears — same destination as the
    // explicit Skip button (covered by the test above).
    await expect(onboarding).toHaveClass(/hidden/, { timeout: 5000 });
    const emptyState = page.locator("#empty-state");
    await expect(emptyState, "empty-state must be revealed after Esc").not.toHaveClass(/hidden/);
    await expect(emptyState).toContainText(/import/i);

    expect(errors, `unexpected page errors: ${errors.join(" | ")}`).toEqual([]);
  });
});
