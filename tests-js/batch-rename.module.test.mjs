// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _setRenamePreviewData,
  getSelectedPhotoPaths,
  hideBatchRenameModal,
  insertRenameToken,
  renderRenamePreview,
  showBatchRenameModal,
  updateRenamePreview,
} from "../bpp/web/static/js/modules/batch-rename.mjs";

beforeEach(() => {
  document.body.innerHTML = `<div id="toast-container"></div>`;
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).selectedPaths = new Set();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).currentGridItems);
  delete (/** @type {any} */ (window).selectedPaths);
});

describe("getSelectedPhotoPaths", () => {
  test("[] when selectedPaths is empty Set", () => {
    expect(getSelectedPhotoPaths()).toEqual([]);
  });

  test("returns the array form of selectedPaths when non-empty", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/a", "/b"]);
    expect(getSelectedPhotoPaths().sort()).toEqual(["/a", "/b"]);
  });

  test("[] when selectedPaths is undefined", () => {
    delete (/** @type {any} */ (window).selectedPaths);
    expect(getSelectedPhotoPaths()).toEqual([]);
  });
});

describe("showBatchRenameModal / hideBatchRenameModal", () => {
  test("show builds the modal on first call with 'all photos' scope when nothing selected", () => {
    /** @type {any} */ (window).currentGridItems = [
      { id: 1, filepath: "/a.jpg" },
      { id: 2, filepath: "/b.jpg" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ mapping: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    showBatchRenameModal();
    expect(document.getElementById("rename-overlay")).toBeTruthy();
    expect(document.getElementById("rename-scope").textContent).toBe("all photos");
    expect(document.getElementById("rename-count").textContent).toBe("2 photos");
    /** @type {HTMLInputElement} */ (document.getElementById("rename-pattern"));
  });

  test("show with selection uses 'N selected' scope label", () => {
    /** @type {any} */ (window).currentGridItems = [
      { id: 1, filepath: "/a.jpg" },
      { id: 2, filepath: "/b.jpg" },
    ];
    /** @type {any} */ (window).selectedPaths = new Set(["/a.jpg"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 }))
    );
    showBatchRenameModal();
    expect(document.getElementById("rename-scope").textContent).toBe("1 selected");
    expect(document.getElementById("rename-count").textContent).toBe("1 photos");
  });

  test("hide adds .hidden class to existing overlay", () => {
    document.body.innerHTML = `<div id="rename-overlay"></div>`;
    hideBatchRenameModal();
    expect(document.getElementById("rename-overlay").classList.contains("hidden")).toBe(true);
  });

  test("hide is a no-op when overlay doesn't exist yet", () => {
    expect(() => hideBatchRenameModal()).not.toThrow();
  });
});

describe("insertRenameToken", () => {
  test("inserts at the cursor position and updates the preview", async () => {
    /** @type {any} */ (window).currentGridItems = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 }))
    );
    showBatchRenameModal();
    const input = /** @type {HTMLInputElement} */ (document.getElementById("rename-pattern"));
    input.value = "abc";
    input.selectionStart = input.selectionEnd = 1;
    insertRenameToken("{X}");
    expect(input.value).toBe("a{X}bc");
    // Caret advances past the inserted token
    expect(input.selectionStart).toBe(4);
  });

  test("no-op when the input is missing", () => {
    expect(() => insertRenameToken("{X}")).not.toThrow();
  });
});

describe("renderRenamePreview", () => {
  test("'No changes' message when nothing is .changed", () => {
    document.body.innerHTML += `<div id="rename-preview"></div>`;
    renderRenamePreview([{ old_filepath: "/a.jpg", new_filename: "a.jpg", changed: false }]);
    expect(document.getElementById("rename-preview").textContent).toContain("No changes");
  });

  test("renders one .rename-row per changed entry, capped at 10", () => {
    document.body.innerHTML += `<div id="rename-preview"></div>`;
    /** @type {any[]} */
    const mapping = Array.from({ length: 15 }, (_, i) => ({
      old_filepath: `/old${i}.jpg`,
      new_filename: `new${i}.jpg`,
      changed: true,
    }));
    renderRenamePreview(mapping);
    expect(document.querySelectorAll(".rename-row")).toHaveLength(10);
    expect(document.querySelector(".rename-more")?.textContent).toContain("+ 5 more");
  });

  test("escapes filenames to prevent XSS", () => {
    document.body.innerHTML += `<div id="rename-preview"></div>`;
    renderRenamePreview([
      {
        old_filepath: "/<script>x.jpg",
        new_filename: "<i>danger</i>.jpg",
        changed: true,
      },
    ]);
    expect(document.getElementById("rename-preview").innerHTML).toContain("&lt;script&gt;x.jpg");
    expect(document.getElementById("rename-preview").innerHTML).toContain(
      "&lt;i&gt;danger&lt;/i&gt;.jpg"
    );
  });

  test("no-op when preview container is missing", () => {
    expect(() =>
      renderRenamePreview([{ old_filepath: "/a.jpg", new_filename: "b.jpg", changed: true }])
    ).not.toThrow();
  });
});

describe("updateRenamePreview", () => {
  test("renders empty-state hint when pattern is blank", async () => {
    document.body.innerHTML += `
      <input id="rename-pattern" value="">
      <div id="rename-preview"></div>
    `;
    _setRenamePreviewData([{ old_filepath: "/a.jpg", new_filename: "x.jpg", changed: true }]);
    await updateRenamePreview();
    expect(document.getElementById("rename-preview").textContent).toContain("Enter a pattern");
  });

  test("renders 'Failed to generate' when fetch errors", async () => {
    document.body.innerHTML += `
      <input id="rename-pattern" value="{name}">
      <div id="rename-preview"></div>
    `;
    /** @type {any} */ (window).currentGridItems = [{ id: 1, filepath: "/a.jpg" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("boom");
      })
    );
    await updateRenamePreview();
    expect(document.getElementById("rename-preview").textContent).toContain("Failed to generate");
  });
});
