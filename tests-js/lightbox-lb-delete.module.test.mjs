// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { _lbTogglePanel, closeLightbox, lbDelete } from "../bpp/web/static/js/modules/lightbox.mjs";
import { setup, teardown, jsonResp, samplePhoto } from "./_lightbox-setup.mjs";

beforeEach(setup);
afterEach(teardown);

describe("lbDelete", () => {
  test("calls deletePhotos and closes", async () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = lbDelete();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    expect(/** @type {any} */ (window).lightboxIdx).toBe(-1);
  });
});

describe("_lbTogglePanel (mobile bottom sheet)", () => {
  test("starts collapsed: panel has no .expanded class on first render", () => {
    const panel = document.getElementById("lightbox-panel");
    expect(panel?.classList.contains("expanded")).toBe(false);
  });

  test("toggles .expanded on the panel element", () => {
    const panel = document.getElementById("lightbox-panel");
    _lbTogglePanel();
    expect(panel?.classList.contains("expanded")).toBe(true);
    _lbTogglePanel();
    expect(panel?.classList.contains("expanded")).toBe(false);
  });

  test("closeLightbox resets .expanded so the next photo starts collapsed", () => {
    const panel = document.getElementById("lightbox-panel");
    _lbTogglePanel();
    expect(panel?.classList.contains("expanded")).toBe(true);
    closeLightbox();
    expect(panel?.classList.contains("expanded")).toBe(false);
  });

  test("noop when the panel element is missing — no throw", () => {
    document.getElementById("lightbox-panel")?.remove();
    expect(() => _lbTogglePanel()).not.toThrow();
  });
});

describe("Picker listener cleanup registry", () => {
  // Source-scan regression for the cleanup-registry pattern. Any
  // future picker that registers document-level listeners must hand
  // its teardown to `_lbActiveCleanups` so `closeLightbox` can drain
  // open pickers, otherwise the listeners outlive the picker.
  //
  // The original offender (`_lbShowFacePicker`) was removed when the
  // photo-menu "Tag person" was routed to `_iphShowTagPicker`
  // (manual person tag, no face hijacking). The registry stays in
  // place because new pickers will need it.

  test("lightbox source contains the cleanups registry", async () => {
    const fs = await import("node:fs/promises");
    const src = await fs.readFile("bpp/web/static/js/modules/lightbox.mjs", "utf-8");
    expect(src).toContain("_lbActiveCleanups");
  });

  test("closeLightbox drains _lbActiveCleanups", async () => {
    // closeLightbox moved to lightbox-actions in the v0.1 lightbox
    // split; the drain loop lives there now.
    const fs = await import("node:fs/promises");
    const src = await fs.readFile("bpp/web/static/js/modules/lightbox-actions.mjs", "utf-8");
    expect(src).toMatch(/for \(const cleanup of \[\.\.\._lbActiveCleanups\]\)/);
  });
});
