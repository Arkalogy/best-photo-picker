// @ts-check
/**
 * The picker reads a row's method value from the entry the server sent,
 * so a plugin embedder (unknown to the built-in map) can be the picked,
 * running one.
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn().mockResolvedValue({}),
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
  petCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/pets/crop/${h}/${i}`,
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(true),
}));

const { _resetFaceEmbedderStateForTests, _seedFaceEmbedderStateForTests, _deriveRowState } =
  await import("../bpp/web/static/js/modules/modals-face-embedders.mjs");

beforeEach(() => {
  document.body.innerHTML = `<div id="face-embedder-picker"></div><div id="toast-container"></div>`;
  _resetFaceEmbedderStateForTests();
});

describe("a plugin face embedder", () => {
  test("is picked and running when its server-sent method value is the active one", () => {
    _seedFaceEmbedderStateForTests({ activeFaceMethod: "myplugin_embed" });
    const row = _deriveRowState(
      {
        id: "myplugin_embedder",
        display_name: "My embedder",
        kind: "face_embedder",
        method_value: "myplugin_embed",
        catalog_on_disk: true,
        is_catalog_entry: true,
        load_state: "ok",
      },
      null
    );
    expect(row.methodValue).toBe("myplugin_embed");
    expect(row.lifecycle).toBe("running");
  });

  test("the built-in table still answers for an entry the server left bare", () => {
    _seedFaceEmbedderStateForTests({ activeFaceMethod: "sface" });
    const row = _deriveRowState(
      { id: "sface_yunet", display_name: "SFace", kind: "face_embedder", load_state: "ok" },
      { status: "ready", files: [{ name: "sface.onnx", exists: true }] }
    );
    expect(row.methodValue).toBe("sface");
    expect(row.lifecycle).toBe("running");
  });
});
