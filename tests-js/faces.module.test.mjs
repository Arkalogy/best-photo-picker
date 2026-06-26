// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _maybeStartClip,
  _resetFacesState,
  applyFaceRecluster,
  clearFaceSelection,
  dismissFaceNudge,
  loadAlbumFaces,
  loadFaceClusters,
  refreshSmartAlbums,
  renderFaceGallery,
  retryFaceExtraction,
  runAutoOptimize,
  startFaceExtraction,
  toggleFace,
  updateFaceStatus,
  updateFaceThresholdLabel,
} from "../bpp/web/static/js/modules/faces.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="modal-overlay">
      <div id="modal-icon"></div>
      <div id="modal-title"></div>
      <div id="modal-body"></div>
      <div id="modal-actions"></div>
    </div>
    <div id="nav-face-boost"><div id="nav-face-boost-chips"></div></div>
    <div id="settings-face-section" style="display:none"></div>
    <div id="face-cluster-val"></div>
    <div id="face-learned-threshold"></div>
    <div id="face-recluster-nudge"></div>
    <input id="face-cluster-slider" type="range" value="0.55" />
    <button id="btn-recluster">Apply</button>
    <input id="param-k" type="number" value="50" />
    <button id="btn-auto-optimize">Auto-optimize weights</button>
    <div id="optimize-status" class="hidden"></div>
    <div id="status-progress"></div>
  `;
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).selectedFaceIds = new Set();
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = null;
  /** @type {any} */ (window).faceRecognitionAvailable = true;
  /** @type {any} */ (window).FACE_MIN_PHOTOS = 4;
  /** @type {any} */ (window).personDisplayName = vi.fn(() => null);
  /** @type {any} */ (window).shortCount = (n) => `${n}`;
  /** @type {any} */ (window).loadAlbumList = vi.fn();
  /** @type {any} */ (window).scheduleRecompute = vi.fn();
  /** @type {any} */ (window).showStatusProgress = vi.fn();
  /** @type {any} */ (window).hideStatusProgress = vi.fn();
  /** @type {any} */ (window)._analyzeStop = vi.fn();
  /** @type {any} */ (window).maybeShowWizard = vi.fn();
  /** @type {any} */ (window).showPeopleView = vi.fn();
  /** @type {any} */ (window).applySettings = vi.fn();
  // jsdom has no EventSource — stub a no-op for SSE-using paths.
  /** @type {any} */ (globalThis).EventSource = class {
    constructor() {
      /** @type {any} */
      this.onmessage = null;
      /** @type {any} */
      this.onerror = null;
    }
    close() {}
  };
  _resetFacesState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "faceClusters",
    "selectedFaceIds",
    "albumList",
    "currentAlbumId",
    "currentView",
    "faceRecognitionAvailable",
    "FACE_MIN_PHOTOS",
    "personDisplayName",
    "shortCount",
    "loadAlbumList",
    "scheduleRecompute",
    "showStatusProgress",
    "hideStatusProgress",
    "_analyzeStop",
    "maybeShowWizard",
    "showPeopleView",
    "applySettings",
    "_dismissedCount",
    "_dismissedFaces",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetFacesState();
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

describe("updateFaceThresholdLabel", () => {
  test("formats numeric value to 2 decimals", () => {
    updateFaceThresholdLabel(0.55);
    expect(document.getElementById("face-cluster-val")?.textContent).toBe("0.55");
  });

  test("accepts string input", () => {
    updateFaceThresholdLabel("0.7");
    expect(document.getElementById("face-cluster-val")?.textContent).toBe("0.70");
  });
});

describe("_maybeStartClip", () => {
  test("returns false when CLIP toggle is disabled", async () => {
    const settings = await import("../bpp/web/static/js/modules/settings-client.mjs");
    settings._setDbSettings({ model_clip: "false" });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const result = await _maybeStartClip();
    expect(result).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    settings._setDbSettings({});
  });

  test("returns true and starts extraction when CLIP is ready to extract", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url).includes("/api/v1/status")) {
        return jsonResp({ clip_available: true, clip_ready: false, clip_extracting: false });
      }
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await _maybeStartClip();
    expect(result).toBe(true);
  });

  test("returns false when CLIP unavailable", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResp({ clip_available: false, clip_ready: false, clip_extracting: false })
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await _maybeStartClip();
    expect(result).toBe(false);
  });
});

describe("startFaceExtraction", () => {
  test("toasts when faceRecognitionAvailable is false", async () => {
    /** @type {any} */ (window).faceRecognitionAvailable = false;
    await startFaceExtraction();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Face recognition requires"
    );
  });

  test("toasts on API error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ error: "no embeddings" }))
    );
    await startFaceExtraction();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't extract faces"
    );
  });

  test("shows consent dialog when models are pending", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url).includes("/api/v1/models/pending"))
        return jsonResp({
          models: [{ name: "YuNet", size_mb: 0.3, host: "media.githubusercontent.com" }],
          total_mb: 0.3,
        });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const appConfirm = vi.fn(async () => true);
    /** @type {any} */ (window).appConfirm = appConfirm;
    await startFaceExtraction();
    expect(appConfirm).toHaveBeenCalledTimes(1);
    const call = /** @type {any[]} */ (appConfirm.mock.calls[0]);
    const opts = call[2] || {};
    expect(opts.bodyHTML).toContain("YuNet");
    expect(opts.bodyHTML).toContain("media.githubusercontent.com");
  });

  test("aborts extract when user declines consent", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url).includes("/api/v1/models/pending"))
        return jsonResp({
          models: [{ name: "X", size_mb: 1, host: "example.com" }],
          total_mb: 1,
        });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    /** @type {any} */ (window).appConfirm = vi.fn(async () => false);
    await startFaceExtraction();
    const calledExtract = fetchMock.mock.calls.some((c) =>
      String(c[0]).includes("/api/v1/faces/extract")
    );
    expect(calledExtract).toBe(false);
  });

  test("skips consent when no models are pending", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url).includes("/api/v1/models/pending"))
        return jsonResp({ models: [], total_mb: 0 });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const appConfirm = vi.fn(async () => true);
    /** @type {any} */ (window).appConfirm = appConfirm;
    await startFaceExtraction();
    expect(appConfirm).not.toHaveBeenCalled();
  });
});

describe("retryFaceExtraction", () => {
  test("toasts on error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ error: "boom" }))
    );
    await retryFaceExtraction();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't retry face extraction"
    );
  });
});

describe("loadFaceClusters", () => {
  test("clears faceClusters when API returns no clusters", async () => {
    /** @type {any} */ (window).faceClusters = [{ cluster_id: 1 }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ clusters: [] }))
    );
    await loadFaceClusters(false);
    expect(/** @type {any} */ (window).faceClusters).toEqual([]);
  });

  test("populates faceClusters and dismissed_count", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        const u = String(url);
        if (u.includes("/clusters")) {
          return jsonResp({
            clusters: [
              {
                cluster_id: 1,
                photo_count: 10,
                representative: { thumb_hash: "h", face_index: 0 },
              },
            ],
            dismissed_count: 3,
          });
        }
        return jsonResp({});
      })
    );
    await loadFaceClusters(false);
    expect(/** @type {any} */ (window).faceClusters).toHaveLength(1);
    expect(/** @type {any} */ (window)._dismissedCount).toBe(3);
  });

  test("after extraction, unhides settings face section", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          clusters: [
            {
              cluster_id: 1,
              photo_count: 10,
              representative: { thumb_hash: "h", face_index: 0 },
            },
          ],
        })
      )
    );
    await loadFaceClusters(true);
    expect(
      /** @type {HTMLElement} */ (document.getElementById("settings-face-section")).style.display
    ).toBe("block");
  });
});

describe("updateFaceStatus", () => {
  test("modal pops when no clusters and showWarning=true", () => {
    /** @type {any} */ (window).faceClusters = [];
    updateFaceStatus(true);
    expect(document.getElementById("modal-overlay")?.classList.contains("visible")).toBe(true);
  });

  test("no modal when clusters exist", () => {
    /** @type {any} */ (window).faceClusters = [{ cluster_id: 1 }];
    updateFaceStatus(true);
    expect(document.getElementById("modal-overlay")?.classList.contains("visible")).toBe(false);
  });

  test("no modal when faceRecognitionAvailable is false", () => {
    /** @type {any} */ (window).faceRecognitionAvailable = false;
    updateFaceStatus(true);
    expect(document.getElementById("modal-overlay")?.classList.contains("visible")).toBe(false);
  });
});

describe("renderFaceGallery", () => {
  test("renders boost-chip per visible cluster", () => {
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 1, photo_count: 10, representative: { thumb_hash: "h1", face_index: 0 } },
      { cluster_id: 2, photo_count: 1, representative: { thumb_hash: "h2", face_index: 0 } },
    ];
    renderFaceGallery();
    // Only cluster 1 is visible (>=4 photos); cluster 2 hidden (<4 photos, no name)
    const chips = document.querySelectorAll(".boost-chip");
    expect(chips).toHaveLength(1);
  });

  test("named clusters appear even with low photo count", () => {
    /** @type {any} */ (window).personDisplayName = (id) => (id === 2 ? "Alice" : null);
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 2, photo_count: 1, representative: { thumb_hash: "h2", face_index: 0 } },
    ];
    renderFaceGallery();
    const chips = document.querySelectorAll(".boost-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain("Alice");
  });

  test("toggles has-selection class on boost section", () => {
    /** @type {any} */ (window).selectedFaceIds = new Set([1]);
    renderFaceGallery();
    expect(document.getElementById("nav-face-boost")?.classList.contains("has-selection")).toBe(
      true
    );
  });

  test("no chips when faceClusters is empty", () => {
    renderFaceGallery();
    expect(document.querySelectorAll(".boost-chip")).toHaveLength(0);
  });
});

describe("toggleFace / clearFaceSelection", () => {
  test("toggleFace adds and removes ids", () => {
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 1, photo_count: 10, representative: { thumb_hash: "h1", face_index: 0 } },
    ];
    toggleFace(1);
    expect(/** @type {Set<number>} */ (/** @type {any} */ (window).selectedFaceIds).has(1)).toBe(
      true
    );
    toggleFace(1);
    expect(/** @type {Set<number>} */ (/** @type {any} */ (window).selectedFaceIds).has(1)).toBe(
      false
    );
  });

  test("clearFaceSelection empties the set", () => {
    /** @type {any} */ (window).selectedFaceIds = new Set([1, 2]);
    clearFaceSelection();
    expect(/** @type {Set<number>} */ (/** @type {any} */ (window).selectedFaceIds).size).toBe(0);
  });
});

describe("loadAlbumFaces", () => {
  test("clears scope when album is 'all'", async () => {
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    await loadAlbumFaces(1);
    // No scope = all clusters visible
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 5, photo_count: 10, representative: { thumb_hash: "h5", face_index: 0 } },
    ];
    renderFaceGallery();
    expect(document.querySelectorAll(".boost-chip")).toHaveLength(1);
  });

  test("scopes gallery to album-faces response", async () => {
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "manual" }];
    /** @type {any} */ (window).currentAlbumId = 7;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ cluster_ids: [5] }))
    );
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 5, photo_count: 10, representative: { thumb_hash: "h5", face_index: 0 } },
      { cluster_id: 9, photo_count: 10, representative: { thumb_hash: "h9", face_index: 0 } },
    ];
    await loadAlbumFaces(7);
    // Only cluster 5 should render
    expect(document.querySelectorAll(".boost-chip")).toHaveLength(1);
  });
});

describe("applyFaceRecluster", () => {
  test("toasts the cluster count on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (String(url).includes("/recluster")) {
          return jsonResp({ clusters: 12 });
        }
        return jsonResp({ clusters: [] });
      })
    );
    await applyFaceRecluster();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Regrouped into 12 clusters"
    );
  });

  test("toasts on API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ error: "boom" }))
    );
    await applyFaceRecluster();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't regroup faces"
    );
  });
});

describe("dismissFaceNudge", () => {
  test("hides the recluster nudge", () => {
    const nudge = /** @type {HTMLElement} */ (document.getElementById("face-recluster-nudge"));
    nudge.style.display = "flex";
    dismissFaceNudge();
    expect(nudge.style.display).toBe("none");
  });
});

describe("refreshSmartAlbums", () => {
  test("calls the refresh API and loadAlbumList", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await refreshSmartAlbums();
    expect(fetchMock).toHaveBeenCalled();
    expect(/** @type {any} */ (window).loadAlbumList).toHaveBeenCalled();
  });
});

describe("runAutoOptimize", () => {
  test("displays status from breakdown on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          settings: { weights: {} },
          breakdown: { avg_quality: 0.85, face_coverage: 0.75 },
        })
      )
    );
    await runAutoOptimize();
    const statusEl = /** @type {HTMLElement} */ (document.getElementById("optimize-status"));
    expect(statusEl.textContent).toContain("85% avg quality");
    expect(statusEl.textContent).toContain("75% face coverage");
  });

  test("surfaces an error toast on API failure + re-enables the button", async () => {
    // apiFetch throws on non-2xx; runAutoOptimize catches → toastError.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "compute failed" }), {
            status: 500,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await runAutoOptimize();
    const toasts = /** @type {HTMLElement} */ (document.getElementById("toast-container"));
    // toastError formats "Couldn't optimize the weights: compute failed — try again"
    expect(toasts.textContent).toContain("optimize the weights");
    expect(toasts.textContent).toContain("compute failed");
    // Button must un-stick on failure.
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("btn-auto-optimize"));
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toBe("Auto-optimize weights");
  });
});
