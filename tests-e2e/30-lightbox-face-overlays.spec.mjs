// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("30 — Lightbox renders face bbox overlays for detected faces", () => {
  test("opening a multi-face photo shows ≥2 face overlay boxes on the image", async ({ page }) => {
    await openApp(page);

    // face_count is the rough analyze-time count; the lightbox overlays
    // come from the extraction pipeline (face_embeddings table). On the
    // synthetic e2e fixture, rough analyze flags some shapes as faces
    // but SCRFD extraction returns 0, so /api/v1/faces/photo returns no
    // boxes to render. Skip when extracted clusters don't exist.
    const clusters = await api(page, "/api/v1/faces/clusters");
    const clusterList = Array.isArray(clusters) ? clusters : clusters.clusters || [];
    test.skip(
      clusterList.length === 0,
      "no face clusters in fixture — overlays have nothing to render"
    );

    const photos = await api(page, "/api/v1/photos?limit=2000");
    const list = photos.photos || photos;

    // Rough face_count is unreliable here: the synthetic fixture's
    // abstract shapes get rough-flagged but extraction returns 0 boxes,
    // and /api/photos ordering varies — so picking by face_count alone
    // sometimes lands on a photo with no overlays (the old flake). Select
    // by what /api/v1/faces/photo ACTUALLY returns; the fixture seed
    // guarantees one photo with two extracted faces (Alice + Bob).
    const candidates = list
      .filter((p) => p.thumb_hash)
      .sort((a, b) => (b.face_count || 0) - (a.face_count || 0))
      .slice(0, 12);
    let target = null;
    let expectedBoxes = 0;
    for (const p of candidates) {
      const data = await api(page, `/api/v1/faces/photo/${p.thumb_hash}`);
      const boxes = (data.faces || []).filter((f) => f.bbox_pct);
      if (boxes.length >= 2) {
        target = p;
        expectedBoxes = boxes.length;
        break;
      }
    }
    test.skip(!target, "no photo with 2+ extracted face boxes in fixture");

    const card = page
      .locator("#photo-grid .card")
      .filter({ has: page.locator(`img[src*="${target.thumb_hash}"]`) })
      .first();
    await card.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => null);
    // No "click any card" fallback — that turned a locate-miss into a
    // wrong-photo run. The target is known to have boxes; click it or fail.
    await card.click();

    await expect(page.locator("#lightbox")).toHaveClass(/visible/);

    // Face overlays render asynchronously after fetching /api/faces/photo.
    const overlays = page.locator(".lb-face-overlay");
    await expect(overlays.first()).toBeAttached({ timeout: 10000 });
    // We selected a photo with exactly `expectedBoxes` renderable boxes,
    // so assert the exact count rather than a soft ≥1.
    await expect(overlays).toHaveCount(expectedBoxes);

    await page.keyboard.press("Escape");
  });
});
