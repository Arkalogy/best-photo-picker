// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  _analyzeStop,
  _navigateToSmartAlbum,
  doRecompute,
  getParams,
  hideStatusProgress,
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
import { setup, teardown, jsonResp } from "./_analysis-setup.mjs";

beforeEach(setup);
afterEach(teardown);

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
    // When /api/v1/models/pending is unreachable, the fallback
    // consent prompt used to be a silent generic dialog. Now it
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

  test("a stale recompute response from a previous scope does not clobber the current selection", async () => {
    // Cold load: the Library recompute (album 1) is still in flight when the
    // user opens a person album (1110). The album's response lands first with
    // its 13 picks; the slow Library response then arrives with 50
    // library-wide picks and must be ignored — otherwise the BPP Picks filter
    // shows only the 1 overlap photo.
    /** @type {any} */ const win = window;
    win.photos = [{ filepath: "/mia1" }, { filepath: "/mia2" }];
    /** @type {Record<string, (v: any) => void>} */
    const resolvers = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (/** @type {string} */ url) =>
          new Promise((resolve) => {
            resolvers[url.includes("/albums/1110/") ? "album" : "library"] = resolve;
          })
      )
    );
    win.currentAlbumId = 1;
    const libraryCall = doRecompute({ delta: true });
    win.currentAlbumId = 1110;
    const albumCall = doRecompute({ delta: true });

    resolvers.album(jsonResp({ scores: {}, selected_paths: ["/mia1", "/mia2"], stats: {} }));
    await albumCall;
    resolvers.library(jsonResp({ scores: {}, selected_paths: ["/mia1", "/other"], stats: {} }));
    await libraryCall;

    expect([...win.selectedPaths]).toEqual(["/mia1", "/mia2"]);
  });

  test("a superseded recompute in the same scope is ignored; the newest wins", async () => {
    /** @type {any} */ const win = window;
    win.photos = [{ filepath: "/a" }, { filepath: "/b" }];
    win.currentAlbumId = 7;
    /** @type {((v: any) => void)[]} */
    const resolvers = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise((resolve) => resolvers.push(resolve)))
    );
    const first = doRecompute({ delta: true });
    const second = doRecompute({ delta: true });
    resolvers[1](jsonResp({ scores: {}, selected_paths: ["/b"], stats: {} }));
    await second;
    resolvers[0](jsonResp({ scores: {}, selected_paths: ["/a"], stats: {} }));
    await first;
    expect([...win.selectedPaths]).toEqual(["/b"]);
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
