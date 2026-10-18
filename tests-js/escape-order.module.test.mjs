// @ts-check
// Escape closes the topmost thing on screen first. A photo selection is
// cleared only when its bar is actually showing and nothing is open over
// it — it used to be wiped before Settings (or any dialog) got to close.
import { beforeEach, describe, expect, test } from "vitest";

import "../bpp/web/static/js/modules/lightbox.mjs";

const win = /** @type {any} */ (window);

function escape() {
  document.body.dispatchEvent(
    new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })
  );
}

beforeEach(() => {
  document.body.innerHTML = `
    <div id="settings-overlay"></div>
    <div id="batch-bar"><span id="batch-count"></span></div>
    <div id="modal-overlay"></div>
  `;
  win.lightboxIdx = -1;
  win.currentGridItems = [];
  win.multiSelected = new Set(["/a.jpg", "/b.jpg"]);
});

describe("Escape with photos selected", () => {
  test("closes Settings first and keeps the selection", () => {
    document.getElementById("settings-overlay")?.classList.add("visible");
    document.getElementById("batch-bar")?.classList.add("visible");
    escape();
    expect(document.getElementById("settings-overlay")?.classList.contains("visible")).toBe(false);
    expect(win.multiSelected.size).toBe(2);
  });

  test("clears the selection once nothing is open over it", () => {
    document.getElementById("batch-bar")?.classList.add("visible");
    escape();
    expect(win.multiSelected.size).toBe(0);
  });

  test("closes an open lightbox and keeps the selection", () => {
    document.body.innerHTML += '<div id="lightbox" class="visible"></div>';
    document.getElementById("batch-bar")?.classList.add("visible");
    win.lightboxIdx = 0;
    win.currentGridItems = [{ filepath: "/a.jpg" }];
    escape();
    expect(win.lightboxIdx).toBe(-1); // closed
    expect(win.multiSelected.size).toBe(2);
  });
});
