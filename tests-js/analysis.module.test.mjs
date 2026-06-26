// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _analyzeStop,
  _getAnalyzePhotoCount,
  analyzeSummaryMessage,
  _navigateToSmartAlbum,
  _refreshStatusRight,
  _resetAnalysisState,
  doRecompute,
  getParams,
  hideStatusProgress,
  listenProgress,
  loadPhotosAndRecompute,
  scheduleRecompute,
  showEmptyLibrary,
  showPreviewGallery,
  showSkeletonGrid,
  showStatusAnalyzing,
  showStatusProgress,
  showTuningState,
  startAnalyze,
  startReanalyze,
  updateStats,
  updateStatusSummary,
} from "../bpp/web/static/js/modules/analysis.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <main>
      <div id="toast-container"></div>
      <div id="empty-state"></div>
      <div id="album-nav"></div>
      <div id="toolbar"></div>
      <div id="photo-grid"></div>
      <div id="status-bar">
        <div id="status-summary"></div>
        <div id="status-right"></div>
        <div id="status-progress" class="hidden">
          <div id="status-progress-text"></div>
          <div id="status-progress-fill"></div>
        </div>
      </div>
      <div id="analyzing-banner"></div>
      <button id="btn-analyze-toolbar"></button>
      <button id="btn-analyze"></button>
      <input id="input-dir" />
      <input id="param-k" type="number" value="50" />
      <input id="sort-by" value="date" />
      <input id="filter-by" value="all" />
    </main>
  `;
  /** @type {any} */ (globalThis).EventSource = class {
    constructor() {
      /** @type {any} */
      this.onmessage = null;
      /** @type {any} */
      this.onerror = null;
    }
    close() {}
  };
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).faceRecognitionAvailable = true;
  /** @type {any} */ (window).faceInstallable = false;
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).selectedFaceIds = new Set();
  /** @type {any} */ (window).activeOperation = null;
  /** @type {any} */ (window).recomputeTimer = null;
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window)._updateVisibleCards = vi.fn();
  /** @type {any} */ (window).updatePicksNavCount = vi.fn();
  /** @type {any} */ (window).cancelOperation = vi.fn();
  /** @type {any} */ (window).switchAlbum = vi.fn();
  /** @type {any} */ (window).loadAlbumList = vi.fn();
  /** @type {any} */ (window).switchToLibrary = vi.fn();
  /** @type {any} */ (window).hide = vi.fn();
  /** @type {any} */ (window).show = vi.fn();
  // ML-consent gate: pretend consent was already granted so
  // startReanalyze() doesn't block on appConfirm. Tests targeting
  // the consent flow itself can override these stubs.
  /** @type {any} */ (window).getSetting = vi.fn(() => "1");
  /** @type {any} */ (window).saveSetting = vi.fn();
  /** @type {any} */ (window).appConfirm = vi.fn(async () => true);
  _resetAnalysisState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  for (const k of [
    "photos",
    "selectedPaths",
    "albumList",
    "currentAlbumId",
    "faceRecognitionAvailable",
    "faceInstallable",
    "favorites",
    "overrides",
    "selectedFaceIds",
    "activeOperation",
    "recomputeTimer",
    "renderGrid",
    "_updateVisibleCards",
    "updatePicksNavCount",
    "cancelOperation",
    "switchAlbum",
    "loadAlbumList",
    "switchToLibrary",
    "hide",
    "show",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetAnalysisState();
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

describe("status helpers", () => {
  test("showStatusProgress unhides bar and sets text + fill", () => {
    showStatusProgress("Analyzing 1/100", 1);
    expect(document.getElementById("status-progress")?.classList.contains("hidden")).toBe(false);
    expect(document.getElementById("status-progress-text")?.textContent).toBe("Analyzing 1/100");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("status-progress-fill")).style.width
    ).toBe("1%");
  });

  test("hideStatusProgress re-hides and zeroes fill", () => {
    showStatusProgress("x", 50);
    hideStatusProgress();
    expect(document.getElementById("status-progress")?.classList.contains("hidden")).toBe(true);
    expect(
      /** @type {HTMLElement} */ (document.getElementById("status-progress-fill")).style.width
    ).toBe("0%");
  });

  test("showStatusAnalyzing(true) renders dot+label, false clears", () => {
    showStatusAnalyzing(true);
    expect(document.querySelector(".analyzing-dot")).toBeTruthy();
    showStatusAnalyzing(false);
    expect(document.getElementById("status-right")?.innerHTML).toBe("");
  });
});

describe("_analyzeStop", () => {
  test("re-enables analyze toolbar button", () => {
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("btn-analyze-toolbar"));
    btn.disabled = true;
    btn.classList.add("running");
    _analyzeStop();
    expect(btn.disabled).toBe(false);
    expect(btn.classList.contains("running")).toBe(false);
  });
});

describe("startReanalyze", () => {
  test("delegates to cancelOperation when analyze is already running", async () => {
    /** @type {any} */ (window).activeOperation = "analyze";
    await startReanalyze();
    expect(/** @type {any} */ (window).cancelOperation).toHaveBeenCalled();
  });

  test("toasts when no library is configured", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (String(url).includes("/api/v1/library/status")) {
          return jsonResp({ library_path: null });
        }
        return jsonResp({});
      })
    );
    await startReanalyze();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "No library configured"
    );
  });

  test("kicks off analyze when library is configured", async () => {
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/v1/library/status")) return jsonResp({ library_path: "/lib" });
      if (u.includes("/api/v1/analyze")) return jsonResp({});
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    await startReanalyze();
    expect(/** @type {any} */ (window).activeOperation).toBe("analyze");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value).toBe(
      "/lib"
    );
  });

  test("shows the concrete model list when downloads are pending", async () => {
    // The new consent prompt — instead of "~50MB will download", the
    // user must see exactly which models from which hosts.
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/v1/models/pending"))
        return jsonResp({
          models: [
            { name: "YuNet face detection", size_mb: 0.3, host: "media.githubusercontent.com" },
            { name: "CLIP visual encoder", size_mb: 336, host: "huggingface.co" },
          ],
          total_mb: 336.3,
        });
      if (u.includes("/api/v1/library/status")) return jsonResp({ library_path: "/lib" });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const appConfirm = vi.fn(async () => true);
    /** @type {any} */ (window).appConfirm = appConfirm;
    await startReanalyze();
    expect(appConfirm).toHaveBeenCalledTimes(1);
    const call = /** @type {any[]} */ (appConfirm.mock.calls[0]);
    const title = call[0];
    // Subtitle is null when bodyHTML is in opts; render the structured
    // list via opts.bodyHTML.
    const opts = call[2] || {};
    const bodyHTML = opts.bodyHTML || "";
    expect(title).toMatch(/Download ML models/);
    expect(bodyHTML).toContain("YuNet face detection");
    expect(bodyHTML).toContain("CLIP visual encoder");
    expect(bodyHTML).toContain("media.githubusercontent.com");
    expect(bodyHTML).toContain("huggingface.co");
    // 336.3 → rounded to "336 MB" because >= 100
    expect(bodyHTML).toContain("336 MB");
  });

  test("aborts analyze when the user declines the consent prompt", async () => {
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
    await startReanalyze();
    // No analyze kicked off — and crucially no fetch to /api/library/status either
    expect(/** @type {any} */ (window).activeOperation).toBeFalsy();
    const calledLibraryStatus = fetchMock.mock.calls.some((c) =>
      String(c[0]).includes("/api/v1/library/status")
    );
    expect(calledLibraryStatus).toBe(false);
  });

  test("fallback consent prompt surfaces the manifest-fetch error reason", async () => {
    // M6: when /api/v1/models/pending is unreachable, the fallback
    // consent prompt used to be a silent generic dialog. After M6 it
    // still asks consent (preserving the safety gate) but surfaces
    // the fetch failure in a faint footer so a maintainer testing a
    // misconfigured build sees the symptom immediately, and so user
    // bug-report screenshots carry the breadcrumb.
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/v1/models/pending"))
        return new Response(JSON.stringify({ error: "registry-down" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });
      if (u.includes("/api/v1/library/status")) return jsonResp({ library_path: "/lib" });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const appConfirm = vi.fn(async () => true);
    /** @type {any} */ (window).appConfirm = appConfirm;
    await startReanalyze();
    expect(appConfirm).toHaveBeenCalledTimes(1);
    const call = /** @type {any[]} */ (appConfirm.mock.calls[0]);
    const [title, subtitle, opts] = call;
    expect(title).toMatch(/Download ML models/);
    // Body lives in opts.bodyHTML so the footer can carry the error reason.
    expect(subtitle).toBeNull();
    const bodyHTML = (opts || {}).bodyHTML || "";
    // Generic message still present so the user knows what they're agreeing to.
    expect(bodyHTML).toContain("download ML models");
    // Degraded-state breadcrumb + the fetch error reason.
    expect(bodyHTML).toContain("Couldn't reach the model registry");
    expect(bodyHTML).toContain("ml-consent-degraded");
  });

  test("skips the consent prompt when the pending list is empty", async () => {
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/v1/models/pending")) return jsonResp({ models: [], total_mb: 0 });
      if (u.includes("/api/v1/library/status")) return jsonResp({ library_path: "/lib" });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const appConfirm = vi.fn(async () => true);
    /** @type {any} */ (window).appConfirm = appConfirm;
    await startReanalyze();
    expect(appConfirm).not.toHaveBeenCalled();
    expect(/** @type {any} */ (window).activeOperation).toBe("analyze");
  });
});

describe("startAnalyze", () => {
  test("toasts when input-dir is empty", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value = "";
    await startAnalyze();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Enter a photo folder path"
    );
  });

  test("disables btn-analyze on start and posts to /api/analyze", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value = "/x";
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await startAnalyze();
    expect(fetchMock).toHaveBeenCalled();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const call = calls.find((c) => String(c[0]).includes("/api/v1/analyze"));
    expect(call).toBeDefined();
    expect(call && call[1].method).toBe("POST");
  });
});

describe("getParams / scheduleRecompute / doRecompute", () => {
  test("getParams reads param-k and selected_faces", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "75";
    /** @type {any} */ (window).selectedFaceIds = new Set([1, 2]);
    const params = getParams();
    expect(params.k).toBe(75);
    expect(params.selected_faces).toEqual([1, 2]);
    expect(params.seed).toBe(42);
  });

  test("getParams omits delta=true when photos array is empty", () => {
    const params = getParams({ delta: true });
    expect(params.delta).toBeUndefined();
  });

  test("getParams adds delta=true when photos array is populated", () => {
    /** @type {any} */ (window).photos = [{ filepath: "/a" }];
    const params = getParams({ delta: true });
    expect(params.delta).toBe(true);
  });

  test("scheduleRecompute fires doRecompute after 150ms", () => {
    const fetchMock = vi.fn(async () =>
      jsonResp({ photos: [], selected_paths: [], stats: { total: 0 } })
    );
    vi.stubGlobal("fetch", fetchMock);
    scheduleRecompute();
    expect(fetchMock).not.toHaveBeenCalled();
    vi.advanceTimersByTime(150);
    expect(fetchMock).toHaveBeenCalled();
  });

  test("doRecompute writes back full photos response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({ photos: [{ filepath: "/x" }], selected_paths: ["/x"], stats: { total: 1 } })
      )
    );
    await doRecompute();
    expect(/** @type {any} */ (window).photos).toHaveLength(1);
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
  });

  test("doRecompute applies delta scores in place", async () => {
    /** @type {any} */ (window).photos = [
      { filepath: "/a", aggregate_score: 0.5 },
      { filepath: "/b", aggregate_score: 0.6 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          scores: { "/a": 0.9 },
          selected_paths: [],
          stats: { total: 2 },
        })
      )
    );
    await doRecompute();
    expect(/** @type {any} */ (window).photos[0].aggregate_score).toBe(0.9);
    expect(/** @type {any} */ (window).photos[1].aggregate_score).toBe(0.6);
  });

  test("doRecompute aborts on error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ error: "boom" }))
    );
    await doRecompute();
    expect(/** @type {any} */ (window).renderGrid).not.toHaveBeenCalled();
  });
});

describe("loadPhotosAndRecompute", () => {
  test("shows skeleton when photos is empty, then doRecompute runs", async () => {
    /** @type {any} */ (window).photos = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({ photos: [{ filepath: "/y" }], selected_paths: [], stats: { total: 1 } })
      )
    );
    await loadPhotosAndRecompute();
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
  });
});

describe("showSkeletonGrid", () => {
  test("renders N skeleton cards", () => {
    showSkeletonGrid(5);
    expect(document.querySelectorAll("#photo-grid .skeleton-card")).toHaveLength(5);
  });

  test("defaults to 12 cards", () => {
    showSkeletonGrid();
    expect(document.querySelectorAll("#photo-grid .skeleton-card")).toHaveLength(12);
  });
});

describe("showPreviewGallery", () => {
  test("populates grid from /api/photos/preview and shows status panels", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ photos: [{ filepath: "/p" }] }))
    );
    await showPreviewGallery(2);
    expect(/** @type {any} */ (window).photos).toHaveLength(1);
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
    expect(/** @type {any} */ (window).show).toHaveBeenCalledWith("photo-grid");
  });
});

describe("showTuningState / showEmptyLibrary", () => {
  test("showTuningState calls show/hide for the right panels", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    showTuningState();
    const showMock = /** @type {any} */ (window).show;
    expect(showMock).toHaveBeenCalledWith("photo-grid");
    expect(showMock).toHaveBeenCalledWith("status-bar");
  });

  test("showEmptyLibrary hides toolbar & shows empty-state", () => {
    showEmptyLibrary();
    expect(/** @type {any} */ (window).hide).toHaveBeenCalledWith("toolbar");
    expect(/** @type {any} */ (window).show).toHaveBeenCalledWith("empty-state");
  });
});

describe("updateStats / updateStatusSummary", () => {
  test("populates status summary with count", () => {
    updateStats({ total: 100 });
    expect(document.getElementById("status-summary")?.textContent).toContain("100 photos");
  });

  test("includes 'X selected' when total_selected provided", () => {
    updateStats({ total: 100, total_selected: 50 });
    expect(document.getElementById("status-summary")?.textContent).toContain("50 selected");
  });

  test("includes favorite count when favorites set is non-empty", () => {
    /** @type {any} */ (window).favorites = new Set(["/a", "/b"]);
    updateStatusSummary({ total: 5 });
    expect(document.getElementById("status-summary")?.textContent).toContain("2 fav");
  });

  test("includes overridden count when overrides has keys", () => {
    /** @type {any} */ (window).overrides = { "/a": "include" };
    updateStatusSummary({ total: 5 });
    expect(document.getElementById("status-summary")?.textContent).toContain("1 overridden");
  });
});

describe("_navigateToSmartAlbum", () => {
  test("calls switchAlbum on matching smart album", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all" },
      { id: 7, album_type: "smart_score" },
    ];
    _navigateToSmartAlbum("smart_score");
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(7);
  });

  test("noop when no matching album", () => {
    _navigateToSmartAlbum("smart_nope");
    expect(/** @type {any} */ (window).switchAlbum).not.toHaveBeenCalled();
  });
});

describe("_refreshStatusRight", () => {
  test("clears status-right when no album", async () => {
    /** @type {any} */ (window).currentAlbumId = null;
    /** @type {HTMLElement} */ (document.getElementById("status-right")).innerHTML = "old";
    await _refreshStatusRight();
    expect(document.getElementById("status-right")?.innerHTML).toBe("");
  });

  test("populates status-right with stats badges", async () => {
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).albumList = [{ id: 5, album_type: "manual", name: "Trip" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          date_min: "2024-01-01",
          date_max: "2024-12-31",
          disk_size: 1024 * 1024 * 5,
          avg_score: 0.85,
          people_count: 3,
          gps_count: 0,
          total: 100,
        })
      )
    );
    await _refreshStatusRight();
    const html = document.getElementById("status-right")?.innerHTML || "";
    expect(html).toContain("85%");
    expect(html).toContain("3 people");
    expect(html).toContain("MB");
  });
});

describe("internal photo count", () => {
  test("starts at 0 and resets on _resetAnalysisState", () => {
    expect(_getAnalyzePhotoCount()).toBe(0);
    _resetAnalysisState();
    expect(_getAnalyzePhotoCount()).toBe(0);
  });
});

describe("M15 — phase-aware SSE handling in listenProgress", () => {
  /** @returns {{src: any, capturedHandler: (ev: any) => void}} */
  function captureSseHandler() {
    /** @type {any} */
    let captured = null;
    /** @type {any} */
    const fakeSrc = {
      close: vi.fn(),
      set onmessage(fn) {
        captured = fn;
      },
      get onmessage() {
        return captured;
      },
      onerror: null,
    };
    /** @type {any} */ (globalThis).EventSource = class {
      constructor() {
        return fakeSrc;
      }
    };
    listenProgress();
    return { src: fakeSrc, capturedHandler: captured };
  }

  function emit(handler, msg) {
    handler({ data: JSON.stringify(msg) });
  }

  test("phase event renders 'label (Step X/Y)' on the status bar", () => {
    const { capturedHandler } = captureSseHandler();
    emit(capturedHandler, {
      type: "phase",
      phase: "scoring",
      label: "Scoring images",
      step: 3,
      of: 5,
    });
    const text = document.getElementById("status-progress-text")?.textContent;
    expect(text).toContain("Scoring images");
    expect(text).toContain("Step 3/5");
  });

  test("phase event without step/of falls back to label only", () => {
    const { capturedHandler } = captureSseHandler();
    emit(capturedHandler, { type: "phase", phase: "scanning", label: "Scanning files" });
    const text = document.getElementById("status-progress-text")?.textContent;
    expect(text).toBe("Scanning files");
  });

  test("scan_progress shows 'Scanning N files (M images found)'", () => {
    const { capturedHandler } = captureSseHandler();
    emit(capturedHandler, { type: "scan_progress", scanned: 1500, matched: 423 });
    const text = document.getElementById("status-progress-text")?.textContent || "";
    expect(text).toContain("1500");
    expect(text).toContain("423");
  });

  test("progress event after a phase still renders correctly (no interference)", () => {
    const { capturedHandler } = captureSseHandler();
    emit(capturedHandler, { type: "phase", phase: "scoring", label: "Scoring", step: 3, of: 5 });
    emit(capturedHandler, { type: "progress", current: 25, total: 100 });
    const text = document.getElementById("status-progress-text")?.textContent || "";
    expect(text).toContain("Analyzing 25/100");
  });
});

describe("analyzeSummaryMessage", () => {
  test("0 photos → 'no photos found' (error), not 'complete — ready'", () => {
    const m = analyzeSummaryMessage({ total: 0, processed: 0 }, 0);
    expect(m.isError).toBe(true);
    expect(m.text).toContain("No photos found to analyze");
    expect(m.text).not.toContain("complete");
  });

  test("photos scored → 'Analysis complete' with the count", () => {
    const m = analyzeSummaryMessage({ total: 50, processed: 50, faces_found: 0 }, 50);
    expect(m.isError).toBe(false);
    expect(m.text).toContain("Analysis complete");
    expect(m.text).toContain("50 photos scored");
  });

  test("includes faces + people when present", () => {
    const m = analyzeSummaryMessage(
      { total: 50, processed: 50, faces_found: 8, face_clusters: 3 },
      50
    );
    expect(m.text).toContain("8 faces, 3 people");
  });

  test("missing done payload → still a safe 'ready' message, not a crash", () => {
    const m = analyzeSummaryMessage(null, 0);
    expect(m.isError).toBe(false);
    expect(m.text).toContain("ready");
  });
});
