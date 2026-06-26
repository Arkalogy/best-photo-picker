// @ts-check
/**
 * Analyze worker SSE plumbing, status-bar progress, "Reanalyze" flow,
 * face-recognition install prompt, recompute (full + delta), tuning
 * state transitions, status-bar summary, and per-album right-side
 * stats badge.
 *
 * Reads/writes many shared globals on `window` (`photos`, `selectedPaths`,
 * `activeOperation`, `recomputeTimer`, `currentAlbumId`, `albumList`,
 * `faceRecognitionAvailable`, `faceInstallable`, `favorites`, `overrides`,
 * `selectedFaceIds`) and calls cross-file helpers (`renderGrid`,
 * `_updateVisibleCards`, `cancelOperation`,
 * `switchAlbum`, `loadAlbumList`, `switchToLibrary`, `hide`, `show`)
 * the same way — all still classic-side.
 */

import { analyzePreflightConsent } from "./analysis-preflight.mjs";
import { apiFetch, authEventSource } from "./api-client.mjs";
import { MONTHS_SHORT, formatDate } from "./date-format.mjs";
import { appConfirm } from "./dialogs.mjs";
import { _doFaceInstall, _promptFaceInstall } from "./analysis-install.mjs";
export { _doFaceInstall, _promptFaceInstall };
import { _maybeStartClip, loadFaceClusters, refreshSmartAlbums } from "./faces.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { loadMemories } from "./memories.mjs";
import { loadPresetList } from "./presets.mjs";
import { removeNudge, showNudge } from "./nudges.mjs";
import { toast, toastError } from "./toast.mjs";
import { syncToolbarK, updateLibStats, updateShowPicksChip } from "./toolbar.mjs";
import { updateDedupStats } from "./clip.mjs";

let _analyzePhotoCount = 0;
/** @type {ReturnType<typeof setTimeout> | null} */
let _previewRefreshTimer = null;
/** @type {any} */
let _lastAnalyzeDone = null;

function _analyzeStart() {
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-analyze-toolbar")
  );
  if (btn) {
    btn.disabled = true;
    btn.classList.add("running");
  }
  _analyzePhotoCount = 0;
}

export function _analyzeStop() {
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-analyze-toolbar")
  );
  if (btn) {
    btn.disabled = false;
    btn.classList.remove("running");
  }
}

function _schedulePreviewRefresh() {
  /** @type {any} */
  const win = window;
  if (_previewRefreshTimer) return;
  _previewRefreshTimer = setTimeout(async () => {
    _previewRefreshTimer = null;
    try {
      const data = await apiFetch("/api/v1/photos/preview");
      if (data.photos && data.photos.length > 0) {
        win.photos = data.photos;
        win.selectedPaths = new Set();
        win.renderGrid?.();
        win.show?.("photo-grid");
        win.show?.("toolbar");
        win.show?.("album-nav");
        win.show?.("status-bar");
        win.hide?.("empty-state");
      }
    } catch {
      /* ignore during analysis */
    }
  }, 1000);
}


export async function startReanalyze() {
  /** @type {any} */
  const win = window;
  if (win.activeOperation === "analyze") {
    win.cancelOperation?.();
    return;
  }
  removeNudge("analyze_photos");

  if (!(await analyzePreflightConsent())) return;

  if (!win.faceRecognitionAvailable) {
    const choice = await _promptFaceInstall();
    if (choice === "install") {
      const installed = await _doFaceInstall();
      if (!installed) return;
      win.faceRecognitionAvailable = true;
    } else if (choice !== "continue") {
      return;
    }
  }

  _analyzeStart();
  try {
    const libStatus = await apiFetch("/api/v1/library/status");
    const libPath = libStatus.library_path;
    if (!libPath) {
      toast("No library configured", true);
      _analyzeStop();
      return;
    }
    const inputDir = /** @type {HTMLInputElement | null} */ (
      document.getElementById("input-dir")
    );
    if (inputDir) inputDir.value = libPath;
    const resp = await apiFetch("/api/v1/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_dir: libPath, recursive: true }),
    });
    if (resp.error) {
      toast(resp.error, true);
      _analyzeStop();
      return;
    }
    win.activeOperation = "analyze";
    showStatusProgress("Analyzing…", 0);
    _listenAnalyzeProgress();
  } catch (e) {
    _analyzeStop();
    toastError("start analysis", e);
  }
}

function _listenAnalyzeProgress() {
  /** @type {any} */
  const win = window;
  const src = authEventSource("/api/v1/analyze/progress");
  src.onmessage = (ev) => {
    const msg = /** @type {any} */ (parseSSE(ev.data));
    if (!msg) return;
    if (msg.type === "phase") {
      // M15: structured phase events let the UI surface a stepper
      // ("Step 3 of 5: Scoring images") instead of a single black-box bar.
      const label = msg.label || msg.phase || "Working…";
      const step = msg.step && msg.of ? ` (Step ${msg.step}/${msg.of})` : "";
      showStatusProgress(`${label}${step}`, 0);
    } else if (msg.type === "scan_progress") {
      // No reliable total during scan — show "Scanning N files (M images found)"
      // and leave the bar indeterminate (0%).
      showStatusProgress(
        `Scanning ${msg.scanned} files (${msg.matched} images found)`,
        0,
      );
    } else if (msg.type === "progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      _analyzePhotoCount = msg.total;
      showStatusProgress(`Analyzing ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "face_start") {
      showStatusProgress("Detecting faces…", 0);
    } else if (msg.type === "face_progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      showStatusProgress(`Detecting faces ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "clip_start") {
      showStatusProgress("Computing semantic search index…", 0);
    } else if (msg.type === "clip_progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      showStatusProgress(`CLIP ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "done") {
      src.close();
      win.activeOperation = null;
      if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
      _previewRefreshTimer = null;
      _lastAnalyzeDone = msg;
      removeNudge("analyze_photos");
      loadPhotosAndRecompute();
      refreshSmartAlbums();
      // Sensitive-photo alert (P0): active notice with a path into the
      // Sensitive album. Only fires on NEW flags (server-side count
      // delta) so re-analysis of an unchanged library stays silent.
      if (msg.sensitive_new > 0) {
        toast(
          `${msg.sensitive_new} photo${msg.sensitive_new !== 1 ? "s" : ""} may be sensitive`,
          "warning",
          { action: { label: "Review", fn: () => win.openSensitiveAlbum?.() } },
        );
      }
      if (msg.faces_found > 0) loadFaceClusters(true);
      else showNudge("export_ready", "nudge-container");
      _maybeStartClip().then((started) => {
        if (!started) {
          _analyzeStop();
          hideStatusProgress();
          _showAnalyzeSummary();
        }
      });
    } else if (msg.type === "error") {
      src.close();
      win.activeOperation = null;
      if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
      _previewRefreshTimer = null;
      hideStatusProgress();
      _analyzeStop();
      toast(msg.message || "Analysis failed", true);
    } else if (msg.type === "cancelled") {
      src.close();
      win.activeOperation = null;
      if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
      _previewRefreshTimer = null;
      hideStatusProgress();
      _analyzeStop();
    } else if (msg.type === "batch_ready") {
      _schedulePreviewRefresh();
    } else if (msg.type === "warning") {
      toast(msg.message || "Warning during analysis", true);
    } else if (msg.type === "status") {
      showStatusProgress(msg.message, 0);
    }
  };
  src.onerror = () => {
    src.close();
    win.activeOperation = null;
    if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
    _previewRefreshTimer = null;
    _analyzeStop();
    hideStatusProgress();
  };
}

/**
 * Decide the post-analyze toast. Pure so every branch is unit-testable
 * (the 0-photo case in particular — see analysis.module.test.mjs).
 * @param {{total?: number, processed?: number, faces_found?: number, face_clusters?: number}|null} done
 *        the analyze `done` SSE payload
 * @param {number} photoCount  photos scored this run (_analyzePhotoCount)
 * @returns {{text: string, isError: boolean}}
 */
export function analyzeSummaryMessage(done, photoCount) {
  // Nothing was found to analyze (empty/wrong folder, or a library with no
  // new photos). Say so plainly — "Analysis complete — ready" on zero photos
  // reads as "it worked" and leaves the user staring at an empty grid.
  if (done && (done.total || 0) === 0 && (done.processed || 0) === 0) {
    return { text: "No photos found to analyze — import photos first.", isError: true };
  }
  const parts = [];
  if (photoCount > 0) parts.push(`${photoCount.toLocaleString()} photos scored`);
  if (done && (done.faces_found || 0) > 0) {
    parts.push(`${done.faces_found} faces, ${done.face_clusters} people`);
  }
  parts.push("ready");
  return { text: "Analysis complete — " + parts.join(", "), isError: false };
}

function _showAnalyzeSummary() {
  const { text, isError } = analyzeSummaryMessage(_lastAnalyzeDone, _analyzePhotoCount);
  toast(text, isError ? true : undefined);
}

export async function startAnalyze() {
  /** @type {any} */
  const win = window;
  const inputEl = /** @type {HTMLInputElement | null} */ (document.getElementById("input-dir"));
  const inputDir = (inputEl?.value || "").trim();
  if (!inputDir) {
    toast("Enter a photo folder path.", true);
    return;
  }
  const btnAnalyze = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-analyze")
  );
  if (btnAnalyze) btnAnalyze.disabled = true;
  win.activeOperation = "analyze";
  showStatusProgress("Analyzing…", 0);

  try {
    await apiFetch("/api/v1/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_dir: inputDir }),
    });
    listenProgress();
  } catch (e) {
    if (btnAnalyze) btnAnalyze.disabled = false;
    win.activeOperation = null;
    hideStatusProgress();
    toastError("start the analysis", e);
  }
}

export function listenProgress() {
  /** @type {any} */
  const win = window;
  /** @type {number | null} */
  let analyzeStart = null;
  const src = authEventSource("/api/v1/analyze/progress");
  src.onmessage = (ev) => {
    const msg = /** @type {any} */ (parseSSE(ev.data));
    if (!msg) return;
    if (msg.type === "phase") {
      // M15: structured phase events drive the high-level stepper.
      const label = msg.label || msg.phase || "Working…";
      const step = msg.step && msg.of ? ` (Step ${msg.step}/${msg.of})` : "";
      showStatusProgress(`${label}${step}`, 0);
    } else if (msg.type === "scan_progress") {
      showStatusProgress(
        `Scanning ${msg.scanned} files (${msg.matched} images found)`,
        0,
      );
    } else if (msg.type === "progress") {
      if (!analyzeStart) analyzeStart = Date.now();
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      _analyzePhotoCount = msg.total;
      let eta = "";
      if (msg.current > 2) {
        const elapsed = (Date.now() - analyzeStart) / 1000;
        const rate = msg.current / elapsed;
        const remaining = Math.round((msg.total - msg.current) / rate);
        if (remaining >= 60) eta = ` — ~${Math.round(remaining / 60)}m left`;
        else if (remaining > 5) eta = ` — ~${remaining}s left`;
      }
      showStatusProgress(`Analyzing ${msg.current}/${msg.total}${eta}`, pct);
    } else if (msg.type === "face_start") {
      showStatusProgress("Detecting faces…", 0);
    } else if (msg.type === "face_progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      showStatusProgress(`Detecting faces ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "clip_start") {
      showStatusProgress("Computing semantic search index…", 0);
    } else if (msg.type === "clip_progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      showStatusProgress(`CLIP ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "done") {
      src.close();
      win.activeOperation = null;
      if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
      _previewRefreshTimer = null;
      _lastAnalyzeDone = msg;
      showTuningState();
      loadPhotosAndRecompute();
      refreshSmartAlbums();
      if (msg.faces_found > 0) loadFaceClusters(true);
    } else if (msg.type === "cancelled") {
      src.close();
      win.activeOperation = null;
      if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
      _previewRefreshTimer = null;
      hideStatusProgress();
    } else if (msg.type === "error") {
      src.close();
      win.activeOperation = null;
      if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
      _previewRefreshTimer = null;
      toast(msg.message || "Analysis error", true);
      hideStatusProgress();
    } else if (msg.type === "batch_ready") {
      _schedulePreviewRefresh();
    } else if (msg.type === "warning") {
      toast(msg.message || "Warning during analysis", true);
    } else if (msg.type === "start") {
      analyzeStart = Date.now();
      showStatusProgress(`Found ${msg.total} images`, 0);
    } else if (msg.type === "status") {
      showStatusProgress(msg.message, 0);
    }
  };
  src.onerror = () => {
    src.close();
    win.activeOperation = null;
    if (_previewRefreshTimer) clearTimeout(_previewRefreshTimer);
    _previewRefreshTimer = null;
    setTimeout(async () => {
      const st = await apiFetch("/api/v1/status");
      if (st.has_analysis) {
        showTuningState();
        loadPhotosAndRecompute();
      } else {
        document.querySelector("main")?.classList.remove("analyzing-pending");
        win.hide?.("analyzing-banner");
        hideStatusProgress();
        showStatusAnalyzing(false);
      }
    }, 1000);
  };
}

/**
 * @param {string} text
 * @param {string | number} pct
 */

import {
  doRecompute,
  getParams,
  loadPhotosAndRecompute,
  scheduleRecompute,
  showSkeletonGrid,
  updateStats,
} from "./analysis-recompute.mjs";
export {
  doRecompute,
  getParams,
  loadPhotosAndRecompute,
  scheduleRecompute,
  showSkeletonGrid,
  updateStats,
};

/** Test-only: read internal _analyzePhotoCount. */
export function _getAnalyzePhotoCount() {
  return _analyzePhotoCount;
}

export function _resetAnalysisState() {
  _analyzePhotoCount = 0;
  _previewRefreshTimer = null;
  _lastAnalyzeDone = null;
}

import {
  _navigateToSmartAlbum,
  _refreshStatusRight,
  hideStatusProgress,
  showEmptyLibrary,
  showPreviewGallery,
  showStatusAnalyzing,
  showStatusProgress,
  showTuningState,
  updateStatusSummary,
} from "./analysis-status.mjs";
export {
  _navigateToSmartAlbum,
  _refreshStatusRight,
  hideStatusProgress,
  showEmptyLibrary,
  showPreviewGallery,
  showStatusAnalyzing,
  showStatusProgress,
  showTuningState,
  updateStatusSummary,
};

