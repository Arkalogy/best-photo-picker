// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  _getAnalyzePhotoCount,
  analyzeSummaryMessage,
  _refreshStatusRight,
  _resetAnalysisState,
  listenProgress,
} from "../bpp/web/static/js/modules/analysis.mjs";
import { setup, teardown, jsonResp } from "./_analysis-setup.mjs";

beforeEach(setup);
afterEach(teardown);

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

describe("Phase-aware SSE handling in listenProgress", () => {
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
