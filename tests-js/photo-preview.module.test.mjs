// @ts-check
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  authedSrc: (/** @type {string} */ s) => s,
}));

import { openPhotoPreview } from "../bpp/web/static/js/modules/photo-preview.mjs";

afterEach(() => {
  document.getElementById("photo-preview-overlay")?.remove();
  document.body.innerHTML = "";
});

describe("openPhotoPreview — full photo so the user can decide", () => {
  test("opens a scrim with the FULL image (/photo/<hash>), not a crop/thumb", () => {
    openPhotoPreview("abc123");
    const ov = document.getElementById("photo-preview-overlay");
    expect(ov).toBeTruthy();
    const img = ov.querySelector("img");
    expect(img.getAttribute("src")).toBe("/photo/abc123");
  });

  test("clicking the scrim closes it", () => {
    openPhotoPreview("h");
    const ov = document.getElementById("photo-preview-overlay");
    ov.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.getElementById("photo-preview-overlay")).toBeNull();
  });

  test("Esc closes the preview (and stops the event so the modal stays open)", () => {
    openPhotoPreview("h");
    const e = new KeyboardEvent("keydown", { key: "Escape", bubbles: true });
    const stop = vi.spyOn(e, "stopImmediatePropagation");
    document.dispatchEvent(e);
    expect(document.getElementById("photo-preview-overlay")).toBeNull();
    expect(stop).toHaveBeenCalled();
  });

  test("empty hash is a no-op (no scrim)", () => {
    openPhotoPreview("");
    expect(document.getElementById("photo-preview-overlay")).toBeNull();
  });

  test("renders a visible close button (discoverable chrome, not just Esc)", () => {
    openPhotoPreview("h");
    const btn = document.querySelector("#photo-preview-overlay .photo-preview-close");
    expect(btn).toBeTruthy();
    // Clicking it bubbles to the overlay handler and closes.
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.getElementById("photo-preview-overlay")).toBeNull();
  });

  test("renders a caption with filename · timestamp · score when meta passed", () => {
    openPhotoPreview("h", "IMG_6346.HEIC", "2025-12-06T11:50:29", 0.8);
    const cap = document.querySelector("#photo-preview-overlay .photo-preview-caption");
    expect(cap).toBeTruthy();
    expect(cap.textContent).toContain("IMG_6346.HEIC");
    expect(cap.textContent).toContain("Dec 6, 2025");
    expect(cap.textContent).toContain("Score 80");
  });

  test("score passed as a numeric string (data-arg coercion) still renders", () => {
    openPhotoPreview("h", "a.jpg", "", "0.5");
    const cap = document.querySelector("#photo-preview-overlay .photo-preview-caption");
    expect(cap.textContent).toContain("Score 50");
  });

  test("no caption when no meta given", () => {
    openPhotoPreview("h");
    expect(document.querySelector("#photo-preview-overlay .photo-preview-caption")).toBeNull();
  });

  test("reopening does not leak a second keydown handler", () => {
    openPhotoPreview("a");
    openPhotoPreview("b"); // reuses the existing overlay, must not double-bind
    const e = new KeyboardEvent("keydown", { key: "Escape", bubbles: true });
    document.dispatchEvent(e);
    expect(document.getElementById("photo-preview-overlay")).toBeNull();
    // A leaked handler would throw on the already-removed node; a clean run
    // dispatching Esc again is a no-op.
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(document.getElementById("photo-preview-overlay")).toBeNull();
  });
});
