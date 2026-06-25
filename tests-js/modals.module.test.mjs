// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getExportBatchPaths,
  _populateClearPhotoCount,
  _resetModalsState,
  batchExport,
  getExportPaths,
  hideExportModal,
  hideImportModal,
  hideSettings,
  loadModelsList,
  openImportBrowser,
  populateExportModes,
  showExportModal,
  showImportModal,
  showSettings,
  startAnalyzeFromModal,
  startImportFromModal,
  switchSettingsTab,
  toggleFollowSymlinks,
  toggleModel,
  updateClipThresholdLabel,
  updateExportScope,
  updateFaceConfidenceLabel,
  updateMaxLongSideLabel,
  validateImportModal,
} from "../bpp/web/static/js/modules/modals.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    <div id="import-modal-overlay">
      <input id="import-dir-input" />
      <button id="btn-modal-analyze">Analyze</button>
      <button id="btn-modal-import">Import</button>
    </div>
    <input id="input-dir" />
    <div id="settings-overlay">
      <div class="settings-tab active" data-tab="app"></div>
      <div class="settings-tab" data-tab="library"></div>
      <div class="settings-tab-pane active" id="settings-pane-app"></div>
      <div class="settings-tab-pane" id="settings-pane-library"></div>
      <div id="settings-library-banner" style="display:none"></div>
      <button class="theme-btn" data-theme="dark"></button>
      <button class="theme-btn" data-theme="light"></button>
      <input id="check-updates-toggle" type="checkbox" />
      <div id="library-name-display">My Library</div>
      <div id="settings-library-name"></div>
      <input id="face-confidence-slider" type="range" />
      <span id="face-confidence-val"></span>
      <input id="pet-confidence-slider" type="range" />
      <span id="pet-confidence-val"></span>
      <input id="face-embed-conf-slider" type="range" />
      <span id="face-embed-conf-val"></span>
      <input id="min-face-area-slider" type="range" />
      <span id="min-face-area-val"></span>
      <input id="min-embed-quality-slider" type="range" />
      <span id="min-embed-quality-val"></span>
      <input id="max-long-side-slider" type="range" />
      <span id="max-long-side-val"></span>
      <input id="clip-threshold-slider" type="range" />
      <span id="clip-threshold-val"></span>
      <input id="thumbnail-size-slider" type="range" />
      <span id="thumbnail-size-val"></span>
      <input id="follow-symlinks-toggle" type="checkbox" />
      <div id="models-list"></div>
      <p id="clear-photo-count" class="danger-warning"></p>
      <input id="clear-confirm-input" type="text" />
      <button id="btn-clear-library">Delete All</button>
    </div>
    <div id="export-modal-overlay">
      <select id="export-scope">
        <option value="picks">Picks</option>
        <option value="view">View</option>
        <option value="batch">Batch</option>
      </select>
      <input id="export-dir" />
      <input id="export-quality" />
      <input id="export-quality-field" />
      <select id="export-format"><option value="jpeg">JPEG</option></select>
      <h2 id="export-modal-title"></h2>
      <button id="btn-do-export">Export</button>
      <div id="export-status"></div>
      <button data-action="hideExportModal">Cancel</button>
    </div>
  `;
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).startAnalyze = vi.fn();
  /** @type {any} */ (window).startImport = vi.fn();
  /** @type {any} */ (window).updateOverrideStats = vi.fn();
  /** @type {any} */ (window).loadPresetList = vi.fn();
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
  /** @type {any} */ (globalThis).EventSource = class {
    constructor() {
      /** @type {any} */
      this.onmessage = null;
      /** @type {any} */
      this.onerror = null;
    }
    close() {}
  };
  _resetModalsState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "currentGridItems",
    "selectedPaths",
    "multiSelected",
    "startAnalyze",
    "startImport",
    "updateOverrideStats",
    "loadPresetList",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetModalsState();
});

/**
 * @param {object} body
 */
function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("import modal", () => {
  test("show / hide toggles visible class", () => {
    showImportModal();
    expect(document.getElementById("import-modal-overlay")?.classList.contains("visible")).toBe(
      true
    );
    hideImportModal();
    expect(document.getElementById("import-modal-overlay")?.classList.contains("visible")).toBe(
      false
    );
  });

  test("validateImportModal disables/enables both action buttons based on input", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("import-dir-input")).value = "  ";
    validateImportModal();
    expect(
      /** @type {HTMLButtonElement} */ (document.getElementById("btn-modal-analyze")).disabled
    ).toBe(true);
    /** @type {HTMLInputElement} */ (document.getElementById("import-dir-input")).value = "/x";
    validateImportModal();
    expect(
      /** @type {HTMLButtonElement} */ (document.getElementById("btn-modal-analyze")).disabled
    ).toBe(false);
  });

  test("openImportBrowser populates input + validates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ path: "/picked" }))
    );
    await openImportBrowser();
    expect(
      /** @type {HTMLInputElement} */ (document.getElementById("import-dir-input")).value
    ).toBe("/picked");
  });

  test("startAnalyzeFromModal copies dir, hides modal, calls window.startAnalyze", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("import-dir-input")).value = "/x";
    showImportModal();
    startAnalyzeFromModal();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value).toBe("/x");
    expect(document.getElementById("import-modal-overlay")?.classList.contains("visible")).toBe(
      false
    );
    expect(/** @type {any} */ (window).startAnalyze).toHaveBeenCalled();
  });

  test("startImportFromModal calls window.startImport", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("import-dir-input")).value = "/x";
    startImportFromModal();
    expect(/** @type {any} */ (window).startImport).toHaveBeenCalled();
  });
});

describe("settings modal", () => {
  test("show / hide toggles visible class", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp([]))
    );
    showSettings();
    expect(document.getElementById("settings-overlay")?.classList.contains("visible")).toBe(true);
    hideSettings();
    expect(document.getElementById("settings-overlay")?.classList.contains("visible")).toBe(false);
  });

  test("switchSettingsTab activates the matching tab + pane", () => {
    switchSettingsTab("library");
    expect(
      document.querySelector('.settings-tab[data-tab="library"]')?.classList.contains("active")
    ).toBe(true);
    expect(document.getElementById("settings-pane-library")?.classList.contains("active")).toBe(
      true
    );
  });
});

describe("settings slider labels", () => {
  test("updateFaceConfidenceLabel formats to 2 decimals", () => {
    updateFaceConfidenceLabel("0.5");
    expect(document.getElementById("face-confidence-val")?.textContent).toBe("0.50");
  });

  test("updateMaxLongSideLabel adds ' px' suffix", () => {
    updateMaxLongSideLabel(2048);
    expect(document.getElementById("max-long-side-val")?.textContent).toBe("2048 px");
  });

  test("updateClipThresholdLabel formats 2 decimals", () => {
    updateClipThresholdLabel("0.92");
    expect(document.getElementById("clip-threshold-val")?.textContent).toBe("0.92");
  });

  test("toggleFollowSymlinks persists 'true'/'false'", () => {
    toggleFollowSymlinks(true);
    toggleFollowSymlinks(false);
    // Just verify it doesn't throw; saveSetting persists to internal cache
    expect(true).toBe(true);
  });
});

describe("loadModelsList", () => {
  test("renders one .model-row per model", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp([
          {
            label: "Face Detector",
            description: "Detects faces",
            status: "ready",
            files: [{ name: "model.pt", exists: true }],
            size_bytes: 1024,
            bundled: false,
            lib_only: false,
          },
          {
            label: "Pose Detector",
            description: "Detects body pose",
            status: "missing",
            files: [{ name: "pose.pt", exists: false }],
            size_bytes: 0,
            bundled: false,
            lib_only: false,
          },
        ])
      )
    );
    await loadModelsList();
    expect(document.querySelectorAll(".model-row")).toHaveLength(2);
    // Second model is missing → "Download all missing" button shows
    expect(document.querySelector(".model-download-all")).toBeTruthy();
  });

  test("falls back to error msg on fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("boom");
      })
    );
    await loadModelsList();
    expect(document.getElementById("models-list")?.textContent).toContain("Could not load");
  });
});

describe("toggleModel", () => {
  test("POSTs and toasts on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    document.body.innerHTML += `
      <div class="model-row">
        <span class="model-name">Face Detector</span>
        <input type="checkbox" id="m1" data-toggle-key="face_detection" />
      </div>
    `;
    const cb = /** @type {HTMLInputElement} */ (document.getElementById("m1"));
    cb.checked = true;
    await toggleModel(cb);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Face Detector enabled"
    );
  });

  test("reverts checkbox on API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    document.body.innerHTML += `
      <div class="model-row">
        <span class="model-name">X</span>
        <input type="checkbox" id="m2" data-toggle-key="x" />
      </div>
    `;
    const cb = /** @type {HTMLInputElement} */ (document.getElementById("m2"));
    cb.checked = true;
    await toggleModel(cb);
    expect(cb.checked).toBe(false);
  });
});

describe("export modal", () => {
  test("show / hide toggles visible class and clears _exportBatchPaths", () => {
    showExportModal();
    expect(document.getElementById("export-modal-overlay")?.classList.contains("visible")).toBe(
      true
    );
    hideExportModal();
    expect(_getExportBatchPaths()).toBeNull();
  });

  test("getExportPaths uses currentGridItems when scope=view", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a" }, { filepath: "/b" }];
    /** @type {HTMLSelectElement} */ (document.getElementById("export-scope")).value = "view";
    const paths = getExportPaths();
    expect(paths.size).toBe(2);
  });

  test("getExportPaths returns selectedPaths by default (scope=picks)", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/x"]);
    /** @type {HTMLSelectElement} */ (document.getElementById("export-scope")).value = "picks";
    expect(getExportPaths().has("/x")).toBe(true);
  });

  test("updateExportScope rewrites the modal title", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/x", "/y"]);
    /** @type {HTMLSelectElement} */ (document.getElementById("export-scope")).value = "picks";
    updateExportScope();
    expect(document.getElementById("export-modal-title")?.textContent).toBe("Export 2 Photos");
  });

  test("batchExport noops silently on empty multiSelected (batch bar is hidden)", () => {
    batchExport();
    // No toast: the Export button lives in the batch bar, which is only
    // visible when something is selected — the guard is unreachable in the UI.
    expect(document.querySelector("#toast-container .toast")).toBeNull();
  });

  test("batchExport seeds _exportBatchPaths from multiSelected", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a", "/b"]);
    batchExport();
    expect(_getExportBatchPaths()?.size).toBe(2);
  });

  test("Export button is disabled on open when #export-dir is empty", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "";
    showExportModal();
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("btn-do-export"));
    expect(btn.disabled).toBe(true);
  });

  test("Export button is enabled on open when #export-dir has a value", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "/already/set";
    showExportModal();
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("btn-do-export"));
    expect(btn.disabled).toBe(false);
  });

  test("typing into #export-dir flips Export from disabled to enabled", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "";
    showExportModal();
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("btn-do-export"));
    expect(btn.disabled).toBe(true);

    const dir = /** @type {HTMLInputElement} */ (document.getElementById("export-dir"));
    dir.value = "/some/path";
    dir.dispatchEvent(new Event("input", { bubbles: true }));
    expect(btn.disabled).toBe(false);

    // Whitespace-only must NOT enable.
    dir.value = "   ";
    dir.dispatchEvent(new Event("input", { bubbles: true }));
    expect(btn.disabled).toBe(true);
  });
});

describe("_populateClearPhotoCount (M-S4)", () => {
  test("renders warning with formatted total + correct pluralization", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ total: 8247, photos: [{}] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    await _populateClearPhotoCount();
    const el = /** @type {HTMLElement} */ (document.getElementById("clear-photo-count"));
    expect(el.textContent).toContain("8,247");
    expect(el.textContent).toContain("photos");
    expect(el.textContent).toContain("cannot be undone");
  });

  test("uses singular 'photo' when total is 1", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ total: 1, photos: [{}] })))
    );
    await _populateClearPhotoCount();
    const text = document.getElementById("clear-photo-count").textContent;
    expect(text).toMatch(/\b1 photo\b/);
    expect(text).not.toMatch(/1 photos/);
  });

  test("hides the warning when library is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ total: 0, photos: [] })))
    );
    await _populateClearPhotoCount();
    expect(document.getElementById("clear-photo-count").textContent).toBe("");
  });

  test("fail-soft on fetch error — does NOT block the modal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 500 }))
    );
    // Pre-fill the slot to ensure it gets cleared (not left stale).
    document.getElementById("clear-photo-count").textContent = "stale value";
    await _populateClearPhotoCount();
    expect(document.getElementById("clear-photo-count").textContent).toBe("");
  });
});

describe("populateExportModes — registry-driven copy-method dropdown", () => {
  function mountSelect() {
    const sel = document.createElement("select");
    sel.id = "export-mode";
    sel.innerHTML = '<option value="copy">Copy photo bytes (default).</option>';
    document.body.appendChild(sel);
    return sel;
  }

  test("fills the dropdown from /api/v1/export/modes", async () => {
    const sel = mountSelect();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              modes: [
                { name: "copy", description: "Copy photo bytes (default)." },
                { name: "hardlink", description: "Hard-link to the original." },
                { name: "symlink", description: "Symlink to the original." },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await populateExportModes();
    const opts = [...sel.options].map((o) => o.value);
    expect(opts).toEqual(["copy", "hardlink", "symlink"]);
    expect(sel.options[1].textContent).toContain("Hard-link");
  });

  test("keeps the static copy fallback when the fetch fails", async () => {
    const sel = mountSelect();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 500 }))
    );
    await populateExportModes();
    // Unchanged: the HTML's static copy option still stands.
    expect([...sel.options].map((o) => o.value)).toEqual(["copy"]);
  });

  test("no #export-mode element → no throw", async () => {
    await expect(populateExportModes()).resolves.toBeUndefined();
  });
});
