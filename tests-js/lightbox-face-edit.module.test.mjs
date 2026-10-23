// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  _lbBeginFaceEdit,
  _lbCommitBboxUpdate,
  _lbEdit,
} from "../bpp/web/static/js/modules/lightbox-face-edit.mjs";
import { jsonResp, setup, teardown } from "./_lightbox-setup.mjs";

beforeEach(setup);
afterEach(teardown);

function box() {
  const el = document.createElement("div");
  el.className = "lb-face-overlay";
  el.style.left = "10%";
  el.style.top = "10%";
  el.style.width = "20%";
  el.style.height = "20%";
  /** @type {HTMLElement} */ (document.querySelector(".lb-img-wrapper")).appendChild(el);
  return el;
}

describe("a face save that lands after the user moved on", () => {
  test("ends only its own edit session and keeps the toast on its own photo", async () => {
    /** @type {any} */
    const win = window;
    const photoA = { id: 1, thumb_hash: "hash-a" };
    const photoB = { id: 2, thumb_hash: "hash-b" };
    win.currentGridItems = [photoA, photoB];
    win.lightboxIdx = 0;

    /** @type {(v: any) => void} */
    let resolveSave = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (String(url).includes("bbox") || String(url).includes("create")) {
          return new Promise((resolve) => {
            resolveSave = () =>
              resolve(jsonResp({ ok: true, face_id: 1, bbox: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 } }));
          });
        }
        return jsonResp({ faces: [], person_tags: [] });
      })
    );

    // Edit a face on photo A and save; the response is slow.
    const faceA = { face_id: 1, face_index: 0, cluster_id: 5 };
    _lbBeginFaceEdit(faceA, "hash-a", box(), null);
    const save = _lbCommitBboxUpdate({ x: 0.1, y: 0.1, w: 0.2, h: 0.2 });

    // Meanwhile the user arrows to B and starts a new edit there.
    win.lightboxIdx = 1;
    const faceB = { face_id: 2, face_index: 0, cluster_id: 6 };
    _lbBeginFaceEdit(faceB, "hash-b", box(), null);
    const sessionB = _lbEdit;
    expect(sessionB?.thumbHash).toBe("hash-b");

    resolveSave();
    await save;

    // B's edit survives A's late response, and no toast offers an action
    // for A on top of B.
    expect(_lbEdit).toBe(sessionB);
    expect(document.querySelector("#toast-container .toast-action")).toBeNull();
  });
});
