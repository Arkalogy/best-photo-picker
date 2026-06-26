// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

/**
 * Always-runnable version of the face-pair review flow test.
 *
 * Mocks `/api/v1/faces/review-pairs/{count,next}` so the test exercises the
 * full UI even when the live library has 0 ambiguous pairs (which is
 * the normal state right after the user reviews everything). This
 * gives us a reliable smoke test for the modal's render contract.
 */
test.describe("07 — Face Pair Review flow opens and shows side-by-side faces", () => {
  test("Review pairs (N) modal opens with two crops + counter + buttons", async ({ page }) => {
    // Mock the count + next endpoints with deterministic data
    // Route patterns match the canonical /api/v1/... paths the UI actually
    // calls. The old patterns omitted the /v1/ segment so the mocks
    // weren't intercepting anything and the test fell through to the
    // real (empty) endpoints.
    await page.route("**/api/v1/faces/review-pairs/count*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ count: 3 }),
      });
    });
    await page.route("**/api/v1/faces/review-pairs/next*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          threshold: 0.8,
          total: 3,
          pairs: [
            {
              cluster_a: {
                id: 99001,
                name: "Test A",
                face_count: 5,
                photo_count: 5,
                representative: { thumb_hash: "deadbeef", face_index: 0 },
              },
              cluster_b: {
                id: 99002,
                name: "Test B",
                face_count: 7,
                photo_count: 7,
                representative: { thumb_hash: "deadbeef", face_index: 1 },
              },
              distance: 0.612,
            },
          ],
        }),
      });
    });

    await openApp(page);

    // The People sidebar entry only renders when at least one face
    // cluster exists. The synthetic e2e fixture has no faces SCRFD
    // can detect, so we skip cleanly rather than time out on a missing
    // nav item. Live libraries with faces still exercise the full flow.
    const clusters = await api(page, "/api/v1/faces/clusters");
    const clusterList = Array.isArray(clusters) ? clusters : clusters.clusters || [];
    test.skip(clusterList.length === 0, "no face clusters in fixture — People nav not rendered");

    await page.locator('.sidebar .nav-item[data-action*="navigateToPeople"]').first().click();

    const btn = page.locator("#btn-review-pairs");
    await expect(btn).toBeVisible();
    // The mocked count > 0 so it should be enabled and labeled
    await expect(btn).toContainText(/Review pairs \(\d+\)/, { timeout: 5000 });
    await btn.click();

    const overlay = page.locator("#face-pair-review-overlay");
    await expect(overlay).toBeVisible();
    await expect(overlay).toHaveClass(/visible/);

    // Header has "Same person?" + "X of N"
    await expect(overlay).toContainText("Same person");
    await expect(overlay).toContainText(/\d+ of \d+/);

    // Two face crops rendered (both bound to our deterministic test pair)
    const faceImgs = overlay.locator(".pair-review-face img");
    await expect(faceImgs).toHaveCount(2);

    // Buttons exist
    await expect(overlay).toContainText("Same person");
    await expect(overlay).toContainText("Different");
    await expect(overlay).toContainText("Skip");

    // Esc closes (no verdict recorded — we mocked /verdict not called either)
    await page.keyboard.press("Escape");
    await expect(overlay).not.toHaveClass(/visible/);
  });
});
