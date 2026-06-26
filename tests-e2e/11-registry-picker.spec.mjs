// @ts-check
/**
 * End-to-end test for the unified model-registry picker + click-through
 * acceptance dialog.
 *
 * Drives the picker through:
 *   1. Settings → Models renders the picker grouped by kind
 *   2. Face entries appear with permissive/restricted/default markers
 *   3. Restricted entries expose "Review & accept…" in their ⋯ menu
 *   4. Opening a face entry → dialog renders WITH biometric block
 *   5. Opening a pet detector → dialog renders WITHOUT biometric block
 *   6. Accept-without-checkboxes → stays open, no row written
 *   7. Accept with all boxes → closes, row appears in acceptance log
 *
 * DOM note: the picker was rebuilt into a table — rows are
 * `tr.fe-row[data-entry-id]`, the model name is `.fe-name`, license is a
 * `.fe-license-{permissive,restricted}` cell, and per-row actions
 * (including "Review & accept the model's license") live behind the ⋯
 * overflow menu (`.fe-overflow-trigger` → `[data-action=
 * openFaceEmbedderAcceptance]`). The Models picker is its own Settings
 * tab (`data-arg0='models'`), no longer under Advanced.
 *
 * Mutating (records acceptance rows) — opted in deliberately. Acceptance
 * log is append-only by design.
 */

import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

const PERMISSIVE_ENTRY_ID = "insightface_scrfd_25g";
const RESTRICTED_NON_FACE_ENTRY_ID = "ultralytics_yolov11n_pets";

/**
 * Open Settings, switch to the Models tab, and wait until the
 * registry picker has rendered its rows.
 *
 * @param {import("@playwright/test").Page} page
 */
async function openModelsTab(page) {
  await page.locator("#btn-settings-toolbar").click();
  await expect(page.locator("#settings-overlay")).toBeVisible({ timeout: 5000 });
  // The Models picker is its own Settings tab.
  await page.locator("[data-action='switchSettingsTab'][data-arg0='models']").click();
  await expect(page.locator("#face-embedder-picker tr.fe-row").first()).toBeVisible({
    timeout: 10000,
  });
}

/** Locate a picker row by its visible model name. */
function rowByName(page, nameText) {
  return page
    .locator("#face-embedder-picker tr.fe-row")
    .filter({ has: page.locator(".fe-name", { hasText: nameText }) });
}

/** Open a restricted entry's acceptance dialog via its ⋯ menu. */
async function openAcceptance(page, nameText) {
  const row = rowByName(page, nameText);
  await row.locator(".fe-overflow-trigger").click();
  // The "Review & accept the model's license" item is now visible.
  await row.locator("[data-action='openFaceEmbedderAcceptance']").click();
  const overlay = page.locator("#fe-acceptance-overlay");
  await expect(overlay).toBeVisible();
  return overlay;
}

test.describe("11 — Model registry picker + acceptance dialog", () => {
  test("Picker renders 9 rows grouped by kind with the right markers", async ({ page }) => {
    const errors = await openApp(page);
    await openModelsTab(page);

    const rows = page.locator("#face-embedder-picker tr.fe-row");
    // 9 entries: 3 face_embedder, 2 face_detector, 1 each of the rest.
    await expect(rows).toHaveCount(9);
    // Grouped by kind — at least the face-embedder section heading shows.
    await expect(page.locator(".fe-kind-title", { hasText: "Face embedders" })).toBeVisible();

    // SFace: the default face embedder. It's restricted-for-attribution
    // (Apache-2.0 ack) under the strictest-defensible-posture, so it shows
    // the Restricted marker, not Permissive.
    const sface = rowByName(page, "SFace");
    await expect(sface).toBeVisible();
    await expect(sface.locator(".fe-name-default")).toBeVisible();
    await expect(sface.locator(".fe-license-restricted")).toBeVisible();
    // A genuinely permissive entry (SCRFD, MIT) shows the Permissive marker.
    await expect(rowByName(page, "SCRFD").locator(".fe-license-permissive")).toBeVisible();

    // buffalo_s: restricted, and its ⋯ menu carries the acceptance action
    // (present in the DOM, hidden until the menu opens).
    const buffalo = rowByName(page, "buffalo_s");
    await expect(buffalo).toBeVisible();
    await expect(buffalo.locator(".fe-license-restricted")).toBeVisible();
    await expect(buffalo.locator("[data-action='openFaceEmbedderAcceptance']")).toHaveCount(1);

    // YOLOv11n: restricted pet detector.
    const yolo = rowByName(page, "YOLOv11n");
    await expect(yolo).toBeVisible();
    await expect(yolo.locator(".fe-license-restricted")).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("Face-entry dialog renders biometric block", async ({ page }) => {
    await openApp(page);
    await openModelsTab(page);

    const overlay = await openAcceptance(page, "buffalo_s");
    await expect(overlay.locator(".fe-acceptance-title")).toHaveText(/buffalo_s/);

    // Biometric block MUST appear on a face entry.
    await expect(
      overlay.locator(".fe-section-title", {
        hasText: "Biometric data responsibility",
      })
    ).toBeVisible();

    await overlay.locator("[data-action='closeFaceEmbedderAcceptance']").click();
    await expect(overlay).not.toBeVisible();
  });

  test("Non-face-entry dialog SUPPRESSES biometric block", async ({ page }) => {
    await openApp(page);
    await openModelsTab(page);

    const overlay = await openAcceptance(page, "YOLOv11n");
    await expect(overlay.locator(".fe-acceptance-title")).toHaveText(/YOLOv11n/);

    // No biometric block on a non-face entry.
    await expect(
      overlay.locator(".fe-section-title", {
        hasText: "Biometric data responsibility",
      })
    ).toHaveCount(0);

    // Shared sections still render (commercial-use definition + the 4
    // acknowledgment checkboxes — no separate-rights box in non-commercial
    // use context).
    await expect(
      overlay.locator(".fe-section-title", { hasText: "Commercial use means" })
    ).toBeVisible();
    await expect(overlay.locator(".fe-checkbox input[type='checkbox']")).toHaveCount(4);

    await overlay.locator("[data-action='closeFaceEmbedderAcceptance']").click();
  });

  test("Accept without checking boxes does not record a row", async ({ page }) => {
    await openApp(page);
    await openModelsTab(page);

    const before = await api(page, "/api/v1/model-registry/acceptance/list");
    const beforeCount = (before?.acceptances || []).filter(
      (a) => a.model_id === RESTRICTED_NON_FACE_ENTRY_ID
    ).length;

    const overlay = await openAcceptance(page, "YOLOv11n");

    // The Accept button stays DISABLED until every box is checked — the
    // current rejection mechanism (the old UI toasted an error instead).
    await expect(overlay.locator("#fe-accept-btn")).toBeDisabled();

    const after = await api(page, "/api/v1/model-registry/acceptance/list");
    const afterCount = (after?.acceptances || []).filter(
      (a) => a.model_id === RESTRICTED_NON_FACE_ENTRY_ID
    ).length;
    expect(afterCount).toBe(beforeCount);

    await overlay.locator("[data-action='closeFaceEmbedderAcceptance']").click();
  });

  test("Accept with all boxes records a row", async ({ page }) => {
    await openApp(page);
    await openModelsTab(page);

    const before = await api(page, "/api/v1/model-registry/acceptance/list");
    const beforeCount = (before?.acceptances || []).filter(
      (a) => a.model_id === RESTRICTED_NON_FACE_ENTRY_ID
    ).length;

    const overlay = await openAcceptance(page, "YOLOv11n");

    const boxes = overlay.locator(".fe-checkbox input[type='checkbox']");
    const count = await boxes.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      await boxes.nth(i).check();
    }

    // Accept is enabled once all boxes are checked.
    await expect(overlay.locator("#fe-accept-btn")).toBeEnabled();
    await overlay.locator("#fe-accept-btn").click();
    await expect(overlay).not.toBeVisible({ timeout: 5000 });

    const after = await api(page, "/api/v1/model-registry/acceptance/list");
    const afterCount = (after?.acceptances || []).filter(
      (a) => a.model_id === RESTRICTED_NON_FACE_ENTRY_ID
    ).length;
    expect(afterCount).toBe(beforeCount + 1);
  });
});

test.describe("11b — Permissive entry has no acceptance action", () => {
  test("SCRFD (permissive) exposes no Review-and-accept item", async ({ page }) => {
    await openApp(page);
    await openModelsTab(page);

    // SCRFD is one of the only two genuinely permissive entries
    // (requires_explicit_ack=false); SFace/dlib/YuNet carry an
    // attribution ack, so they're NOT the permissive case to test here.
    const scrfd = rowByName(page, "SCRFD");
    await expect(scrfd).toBeVisible();
    await expect(scrfd.locator(".fe-license-permissive")).toBeVisible();
    // Permissive entries never carry the acceptance action (not even
    // hidden in the ⋯ menu).
    await expect(scrfd.locator("[data-action='openFaceEmbedderAcceptance']")).toHaveCount(0);
    expect(PERMISSIVE_ENTRY_ID).toBe("insightface_scrfd_25g");
  });
});
