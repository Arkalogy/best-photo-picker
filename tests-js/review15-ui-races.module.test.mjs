// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(),
  authedSrc: (/** @type {string} */ u) => u,
  faceCropUrl: () => "",
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast: vi.fn(), toastError: vi.fn() }));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";

/** @type {any} */
const win = window;

beforeEach(() => {
  document.body.innerHTML = '<div id="photo-grid"></div><div id="lb-editor"></div>';
  win.currentGridItems = [
    { filepath: "/a.jpg", thumb_hash: "ha" },
    { filepath: "/b.jpg", thumb_hash: "hb" },
  ];
  win.lightboxIdx = 0;
  win.editorActive = false;
});
afterEach(() => {
  vi.mocked(apiFetch).mockReset();
  document.body.innerHTML = "";
});

describe("editor open/save races", () => {
  test("an edits response for a photo the lightbox left is dropped", async () => {
    const { openEditor } = await import("../bpp/web/static/js/modules/editor.mjs");
    let release = () => {};
    vi.mocked(apiFetch).mockImplementationOnce(
      () =>
        new Promise((r) => {
          release = () => r({ edits: { rotation: 90 } });
        })
    );
    const p = openEditor();
    win.lightboxIdx = 1; // the user moved on while the request was in flight
    release();
    await p;
    expect(win.editorActive).toBe(false);
  });

  test("Save writes to the photo the editor was opened on", async () => {
    const mod = await import("../bpp/web/static/js/modules/editor.mjs");
    vi.mocked(apiFetch).mockResolvedValueOnce({ edits: {} });
    await mod.openEditor();
    expect(win.editorActive).toBe(true);
    win.editorEdits = { ...win.editorEdits, rotation: 90 }; // something to save
    win.lightboxIdx = 1;
    vi.mocked(apiFetch).mockResolvedValue({});
    await mod.closeEditor(true);
    await Promise.resolve();
    const save = vi
      .mocked(apiFetch)
      .mock.calls.find(([u]) => String(u).includes("/api/v1/photos/save-edits"));
    expect(save).toBeTruthy();
    expect(JSON.parse(/** @type {any} */ (save)[1].body).filepath).toBe("/a.jpg");
  });
});
