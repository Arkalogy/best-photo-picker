// @ts-check
import { afterEach, describe, expect, test, vi } from "vitest";

// _closeReviewOverlay calls loadFaceClusters (faces.mjs) → apiFetch; stub it.
vi.mock("../bpp/web/static/js/modules/faces.mjs", () => ({
  loadFaceClusters: vi.fn(),
  refreshSmartAlbums: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(async () => ({})),
  authedSrc: (/** @type {string} */ s) => s,
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast: vi.fn(), toastError: vi.fn() }));
vi.mock("../bpp/web/static/js/modules/albums.mjs", () => ({ loadAlbumList: vi.fn() }));
vi.mock("../bpp/web/static/js/modules/people.mjs", () => ({
  getPersonAlbumId: vi.fn(),
  getPersonName: vi.fn(),
  personDisplayName: vi.fn(),
}));

import {
  _showReviewOverlay,
  _closeReviewOverlay,
} from "../bpp/web/static/js/modules/people-review.mjs";

afterEach(() => {
  _closeReviewOverlay();
  document.getElementById("face-review-overlay")?.remove();
  document.body.innerHTML = "";
});

/** Press Escape at the document (bubbles; capture handler should catch it). */
function pressEsc() {
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
}

describe("People-review modal: Esc-to-close", () => {
  test("Esc closes the modal (every other modal does)", () => {
    _showReviewOverlay();
    const ov = document.getElementById("face-review-overlay");
    expect(ov.classList.contains("visible")).toBe(true);
    pressEsc();
    expect(ov.classList.contains("visible")).toBe(false);
  });

  test("Esc clears the name autocomplete first, leaving the modal open", () => {
    _showReviewOverlay();
    const ov = document.getElementById("face-review-overlay");
    // Simulate an open autocomplete dropdown inside the card.
    ov.innerHTML = '<div id="review-autocomplete"><div class="opt">Leo</div></div>';
    pressEsc();
    expect(document.getElementById("review-autocomplete").innerHTML).toBe("");
    expect(ov.classList.contains("visible")).toBe(true); // modal stays open
    // Second Esc now closes it.
    pressEsc();
    expect(ov.classList.contains("visible")).toBe(false);
  });

  test("handler is removed on close (no leak after reopen)", () => {
    _showReviewOverlay();
    _closeReviewOverlay();
    // With the overlay closed/not-visible, a stray Esc must be a no-op
    // (handler returns early on no visible overlay) — no throw.
    pressEsc();
    expect(document.getElementById("face-review-overlay")?.classList.contains("visible")).toBe(
      false
    );
  });
});
