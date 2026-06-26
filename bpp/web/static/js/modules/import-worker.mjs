// @ts-check
/**
 * Import flow + SSE progress consumer.
 *
 * Kicks off `/api/v1/import`, then opens an EventSource on
 * `/api/v1/import/progress` and dispatches each message type to the
 * matching UI update. Heavy DOM coupling via classic functions
 * (`showStatusProgress` / `hideStatusProgress` / `showPreviewGallery`
 *  / `advanceTourToPhase` / `loadPhotosAndRecompute` / etc.) — those
 * are read off `window` since their migration hasn't landed.
 */

import { apiFetch, authEventSource } from "./api-client.mjs";
import { state } from "./state.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { showNudge } from "./nudges.mjs";
import { toast, toastError } from "./toast.mjs";
import { esc } from "./text-format.mjs";

/**
 * Human-readable label per server-side phase. The server emits 7
 * distinct phases during analyze (importing / scanning / models /
 * scoring / analyzing / faces / clip) — collapsing every non-import
 * one into a single "Analyzing…" label made the screen look frozen
 * for the entire ~6s cold-start window between disk-scan + model
 * download + subprocess warmup. Specific labels per phase produce
 * visible motion through the gap and tell the user what step is
 * actually taking time. Unknown phases fall back to "Analyzing…".
 *
 * @param {string} phase
 * @returns {string}
 */
export function _phaseLabel(phase) {
  /** @type {Record<string, string>} */
  const labels = {
    importing: "Importing photos…",
    scanning: "Finding photos…",
    models: "Loading ML models…",
    scoring: "Scoring photos…",
    analyzing: "Preparing analysis…",
    faces: "Detecting faces…",
    clip: "Computing semantic search index…",
  };
  return labels[phase] || "Analyzing…";
}

/**
 * Kick off an import. Reads the source dir from `#input-dir`.
 * Sets `state.activeOperation = "import"` so the rest of the UI
 * (cancel button, status bar) knows what's running.
 */
export async function startImport() {
  const inputEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("input-dir")
  );
  const inputDir = inputEl?.value.trim();
  if (!inputDir) {
    toast("Enter a photo folder path.", true);
    return;
  }
  /** @type {any} */
  const win = window;
  win.activeOperation = "import";
  win.showStatusProgress?.("Importing…", 0);

  /** @type {any} */
  const winOpts = window;
  let resp;
  try {
    resp = await apiFetch("/api/v1/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_dir: inputDir,
        import_live_photo_sidecars: winOpts.importLivePhotoSidecars ?? false,
      }),
    });
  } catch (e) {
    toastError("start the import", e);
    win.activeOperation = null;
    win.hideStatusProgress?.();
    return;
  }

  if (resp.error) {
    toast(resp.error, true);
    win.activeOperation = null;
    win.hideStatusProgress?.();
    return;
  }
  listenImportProgress();
}

/**
 * Open the SSE stream for import progress. Each message type maps to
 * a different UI step. The SSE closes itself on `done`, `cancelled`,
 * or `error`.
 */
export function listenImportProgress() {
  /** @type {any} */
  const win = window;
  const src = authEventSource("/api/v1/import/progress");

  src.onmessage = (ev) => {
    const msg = /** @type {any} */ (parseSSE(ev.data));
    if (!msg) return;
    if (msg.type === "import_progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      win.showStatusProgress?.(`Importing ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "analyze_progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      const main = document.querySelector("main");
      if (main?.classList.contains("analyzing-pending")) {
        const banner = document.getElementById("analyzing-banner");
        if (banner) {
          banner.innerHTML =
            `Analyzing ${msg.current}/${msg.total}…<div class="ab-progress">` +
            `<div class="ab-fill" id="ab-fill" style="width:${pct}%"></div></div>`;
        }
      }
      win.showStatusProgress?.(`Analyzing ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "phase") {
      win.showStatusProgress?.(_phaseLabel(msg.phase), 0);
      // Cold-start gap: when phase flips to "analyzing", the first
      // analyze_progress event won't arrive until process_one() finishes
      // photo 1 — and on the first import of the session that includes
      // ML model warmup (SCRFD + SFace), which adds ~3s of silent stall
      // where the banner's progress bar sits empty. Render an
      // indeterminate state immediately so the user sees motion within
      // ~50ms instead of a static "Analyzing photos…" line. The first
      // analyze_progress event replaces this innerHTML and transitions
      // back to the determinate "1/N" bar.
      if (msg.phase === "analyzing") {
        const main = document.querySelector("main");
        if (main?.classList.contains("analyzing-pending")) {
          const banner = document.getElementById("analyzing-banner");
          if (banner) {
            banner.innerHTML =
              `Preparing analysis…<div class="ab-progress indeterminate">` +
              `<div class="ab-fill"></div></div>`;
          }
        }
      }
    } else if (msg.type === "status") {
      // Long-step / model-preflight status (e.g. "Downloading SCRFD face
      // detector (3 MB)…"). Without this branch these were dropped, leaving
      // a silent multi-second gap during first-run model warmup — a
      // "nothing should be silent" violation. Surface in the status bar +
      // the analyzing banner (indeterminate, since these have no %).
      if (msg.message) {
        win.showStatusProgress?.(msg.message, 0);
        const main = document.querySelector("main");
        if (main?.classList.contains("analyzing-pending")) {
          const banner = document.getElementById("analyzing-banner");
          if (banner) {
            banner.innerHTML =
              `${esc(msg.message)}<div class="ab-progress indeterminate">` +
              `<div class="ab-fill"></div></div>`;
          }
        }
      }
    } else if (msg.type === "warning") {
      // Non-fatal preflight warning (e.g. a model download failed → the run
      // falls back to a degraded path). Surface it so the user knows why a
      // capability may be missing, instead of silently dropping the message.
      if (msg.message) toast(msg.message);
    } else if (msg.type === "import_done") {
      win.showPreviewGallery?.(msg.imported);
      win.advanceTourToPhase?.(2);
      showNudge("analyze_photos", "nudge-container");
      _showImportSummary(msg);
    } else if (msg.type === "done") {
      src.close();
      win.activeOperation = null;
      document.querySelector("main")?.classList.remove("analyzing-pending");
      win.hide?.("analyzing-banner");
      win.showTuningState?.();
      win.loadPhotosAndRecompute?.();
      win.refreshSmartAlbums?.();
      if (win.faceRecognitionAvailable) win.startFaceExtraction?.();
      win._analyzePhotoCount = msg.analyzed || 0;
      const maybeStartClip = win._maybeStartClip;
      if (typeof maybeStartClip === "function") {
        maybeStartClip().then(/** @param {boolean} started */ (started) => {
          if (!started) {
            win._analyzeStop?.();
            win.hideStatusProgress?.();
          }
        });
      } else {
        win._analyzeStop?.();
        win.hideStatusProgress?.();
      }
      win.advanceTourToPhase?.(3);
    } else if (msg.type === "cancelled") {
      src.close();
      win.activeOperation = null;
      document.querySelector("main")?.classList.remove("analyzing-pending");
      win.hide?.("analyzing-banner");
      win.hideStatusProgress?.();
      win.showStatusAnalyzing?.(false);
    } else if (msg.type === "error") {
      src.close();
      win.activeOperation = null;
      document.querySelector("main")?.classList.remove("analyzing-pending");
      win.hide?.("analyzing-banner");
      toast(msg.message || "Import error", true);
      win.hideStatusProgress?.();
      win.showStatusAnalyzing?.(false);
    }
  };

  src.onerror = () => {
    src.close();
    win.activeOperation = null;
    setTimeout(async () => {
      const st = await apiFetch("/api/v1/status");
      if (st.has_analysis) {
        win.showTuningState?.();
        win.loadPhotosAndRecompute?.();
      } else {
        document.querySelector("main")?.classList.remove("analyzing-pending");
        win.hide?.("analyzing-banner");
        win.hideStatusProgress?.();
        win.showStatusAnalyzing?.(false);
      }
    }, 1000);
  };
}

/**
 * Stop the current import or analyze. Reads `state.activeOperation`
 * to pick the right cancel endpoint.
 */
export async function cancelOperation() {
  /** @type {any} */
  const win = window;
  const url = win.activeOperation === "import" ? "/api/v1/import/cancel" : "/api/v1/analyze/cancel";
  win.showStatusProgress?.("Stopping…", 0);
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-analyze-toolbar")
  );
  if (btn) btn.disabled = true;
  try {
    await apiFetch(url, { method: "POST" });
  } catch (e) {
    toastError("stop the operation", e);
  }
}

/**
 * Render the import-summary toast. Counts duplicates and errors
 * separately. Shown on the `import_done` SSE message.
 *
 * @param {{ imported: number, skipped?: number, errors?: number, batch_name?: string }} msg
 */
export function _showImportSummary(msg) {
  if (msg.imported === 0 && (msg.skipped || 0) === 0 && (msg.errors || 0) === 0) {
    toast("No supported photos found in this folder.", true);
    return;
  }
  let text = `Imported ${msg.imported} photo${msg.imported === 1 ? "" : "s"}`;
  if (msg.batch_name) text += ` from "${msg.batch_name}"`;
  /** @type {string[]} */
  const parts = [];
  if ((msg.skipped || 0) > 0) {
    parts.push(`${msg.skipped} duplicate${msg.skipped === 1 ? "" : "s"} skipped`);
  }
  if ((msg.errors || 0) > 0) {
    parts.push(`${msg.errors} error${msg.errors === 1 ? "" : "s"}`);
  }
  if (parts.length) text += ` (${parts.join(", ")})`;
  toast(text, msg.imported === 0 ? "warning" : undefined);
}
