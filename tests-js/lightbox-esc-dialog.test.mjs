// @ts-check
import { beforeEach, describe, expect, test, vi } from "vitest";

// Import order mirrors the app: lightbox registers its document keydown at
// module init; the dialog registers its capture handler at show time.
import "../bpp/web/static/js/modules/lightbox.mjs";
import { appConfirm } from "../bpp/web/static/js/modules/dialogs.mjs";

describe("Escape with confirm dialog open over the lightbox", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="lightbox" class="visible"><img id="lb-img"></div>
      <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    `;
    /** @type {any} */ (window).lightboxIdx = 0;
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a.jpg", thumb_hash: "h" }];
    /** @type {any} */ (window).editorActive = false;
    /** @type {any} */ (window).closeLightbox = vi.fn();
  });

  test("Escape closes ONLY the dialog, not the lightbox", async () => {
    const p = appConfirm("Delete this photo?");
    expect(document.getElementById("confirm-overlay")?.classList.contains("visible")).toBe(true);
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })
    );
    const ok = await p;
    expect(ok).toBe(false);
    expect(document.getElementById("confirm-overlay")?.classList.contains("visible")).toBe(false);
    // The lightbox must still be open.
    expect(/** @type {any} */ (window).closeLightbox).not.toHaveBeenCalled();
    expect(document.getElementById("lightbox")?.classList.contains("visible")).toBe(true);
  });
});
