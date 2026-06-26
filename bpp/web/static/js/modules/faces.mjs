// @ts-check
/**
 * Face cluster management — extraction lifecycle (SSE progress),
 * cluster loading, recluster controls, the boost-chip face gallery,
 * face-selection toggling, smart-album refresh, and the auto-optimize
 * weights flow.
 *
 * Reads/writes a few shared globals via `window` (`faceClusters`,
 * `selectedFaceIds`, `currentAlbumId`, `albumList`, `currentView`,
 * `faceRecognitionAvailable`, `_dismissedCount`, `_dismissedFaces`,
 * `FACE_MIN_PHOTOS`) since they're still in classic land. All cross-file
 * helpers it calls (`renderFaceGallery`, `showPeopleView`, `applySettings`,
 * `loadAlbumList`, `scheduleRecompute`, `personDisplayName`, `shortCount`,
 * `showStatusProgress`, `hideStatusProgress`, `_analyzeStop`,
 * `maybeShowWizard`) are also still classic-global; we look them up on
 * window at call time.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { showModal } from "./modal.mjs";
import {getSetting, saveSetting} from "./settings-client.mjs";
import { showNudge } from "./nudges.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";




export async function refreshSmartAlbums() {
  /** @type {any} */
  const win = window;
  // Project convention: nothing should be silent — the smart-album refresh can take
  // 10–30s on a 50k-photo library while it re-runs every registered domain.
  // Show it in the status bar (indeterminate) rather than a toast pair: the
  // completed result is the re-rendered sidebar, so no "done" toast is needed
  // — only the bar while it runs and an error toast on failure.
  win.showStatusProgress?.("Refreshing smart albums…", 0);
  try {
    await apiFetch("/api/v1/albums/refresh-smart", { method: "POST" });
    await win.loadAlbumList?.();
  } catch (e) {
    console.warn("Failed to refresh smart albums:", e);
    toastError("refresh smart albums", e);
  } finally {
    win.hideStatusProgress?.();
  }
}

/**
 * @param {boolean} [afterExtraction]
 */
export async function loadFaceClusters(afterExtraction) {
  // Protection B: wrap the actual loader so a /api/v1/faces/clusters
  // 500 (Jun-2 incident) can't take down the whole sidebar by
  // throwing out of the orchestrator. wrapSectionLoader catches +
  // logs + renders an error pill in the People section + leaves
  // the other sections intact.
  const { wrapSectionLoader } = await import("./sidebar-safety.mjs");
  return wrapSectionLoader("faces", () => _loadFaceClustersInner(afterExtraction));
}

/** @param {boolean} [afterExtraction] */
async function _loadFaceClustersInner(afterExtraction) {
  /** @type {any} */
  const win = window;
  const data = await apiFetch("/api/v1/faces/clusters");
  if (!data.clusters || data.clusters.length === 0) {
    win.faceClusters = [];
    updateFaceStatus(afterExtraction);
    return;
  }
  win.faceClusters = data.clusters;
  win._dismissedCount = data.dismissed_count || 0;
  win._dismissedFaces = null;
  renderFaceGallery();
  if (win.currentView === "people") win.showPeopleView?.();
  if (afterExtraction) {
    const el = document.getElementById("settings-face-section");
    if (el) el.style.display = "block";
    await refreshSmartAlbums();
    showNudge("pick_people", "nudge-container");
    win.maybeShowWizard?.();
  }
  loadFaceThresholdInfo();
}

/**
 * @param {boolean | undefined} showWarning
 */
export function updateFaceStatus(showWarning) {
  /** @type {any} */
  const win = window;
  const clusters = /** @type {any[]} */ (win.faceClusters || []);
  if (showWarning && clusters.length === 0 && win.faceRecognitionAvailable) {
    showModal(
      "\u{1F645}",
      "No Faces Detected",
      "No recognizable faces were found in your photos. The Faces filter won't be available for this collection."
    );
  }
}



/**
 * @param {string | number} val
 */
export function updateFaceThresholdLabel(val) {
  const el = document.getElementById("face-cluster-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2);
}

export async function applyFaceRecluster() {
  /** @type {any} */
  const win = window;
  const slider = /** @type {HTMLInputElement | null} */ (
    document.getElementById("face-cluster-slider")
  );
  if (!slider) return;
  const threshold = parseFloat(slider.value);
  const btn = /** @type {HTMLButtonElement | null} */ (document.getElementById("btn-recluster"));
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Applying…";
  }
  const resp = await apiFetch("/api/v1/faces/recluster", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threshold }),
  });
  if (btn) {
    btn.disabled = false;
    btn.textContent = "Apply";
  }
  if (resp.error) {
    toastError("regroup faces", new Error(resp.error));
    return;
  }
  toast(`Regrouped into ${resp.clusters} clusters`);
  await loadFaceClusters(false);
  await win.loadAlbumList?.();
}

let _faceNudgeDismissed = false;

export async function loadFaceThresholdInfo() {
  try {
    const data = await apiFetch("/api/v1/faces/feedback/stats");
    const el = document.getElementById("face-learned-threshold");
    if (el && data.corrections > 0 && data.source !== "default") {
      const n = data.corrections;
      el.textContent =
        "Suggested setting (" +
        data.threshold.toFixed(2) +
        ") learned from your " +
        n +
        " " +
        (n === 1 ? "merge/split" : "merges/splits") +
        ".";
      el.style.display = "block";
    } else if (el) {
      el.style.display = "none";
    }
    const nudge = document.getElementById("face-recluster-nudge");
    if (nudge && data.nudge_recluster && !_faceNudgeDismissed) {
      nudge.style.display = "flex";
    } else if (nudge) {
      nudge.style.display = "none";
    }
  } catch {
    /* not critical */
  }
}

export function dismissFaceNudge() {
  _faceNudgeDismissed = true;
  const nudge = document.getElementById("face-recluster-nudge");
  if (nudge) nudge.style.display = "none";
}

/** @type {Set<number> | null} — face cluster IDs in current album, null = show all */
let albumFaceClusterIds = null;

/**
 * @param {number} albumId
 */
export async function loadAlbumFaces(albumId) {
  /** @type {any} */
  const win = window;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = albums.find((a) => a.id === albumId);
  if (!album || album.album_type === "all") {
    albumFaceClusterIds = null;
    renderFaceGallery();
    return;
  }
  try {
    const data = await apiFetch(`/api/v1/albums/${albumId}/faces`);
    if (win.currentAlbumId !== albumId) return; // stale
    albumFaceClusterIds = new Set(data.cluster_ids || []);
    renderFaceGallery();
  } catch {
    albumFaceClusterIds = null;
    renderFaceGallery();
  }
}

export function renderFaceGallery() {
  /** @type {any} */
  const win = window;
  const gallery = document.getElementById("nav-face-boost-chips");
  if (!gallery) return;

  const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
  const selectedFaceIds = /** @type {Set<number>} */ (win.selectedFaceIds || new Set());
  const minPhotos = /** @type {number} */ (win.FACE_MIN_PHOTOS ?? 4);
  const personDisplayName = /** @type {(id: number) => string | null} */ (
    win.personDisplayName || (() => null)
  );
  const shortCount = /** @type {(n: number) => string} */ (win.shortCount || ((n) => String(n)));

  let visible = faceClusters.filter(
    (c) => (c.photo_count || 0) >= minPhotos || personDisplayName(c.cluster_id)
  );
  if (albumFaceClusterIds) {
    visible = visible.filter((c) => albumFaceClusterIds.has(c.cluster_id));
  }
  gallery.innerHTML = visible
    .map((c) => {
      const rep = c.representative;
      const sel = selectedFaceIds.has(c.cluster_id) ? " selected" : "";
      const name = personDisplayName(c.cluster_id);
      const tip = name ? `${name} — ${c.photo_count} photos` : `${c.photo_count} photos`;
      const label = name || shortCount(c.photo_count);
      return `<div class="boost-chip${sel}" data-action="toggleFace" data-arg0="${c.cluster_id}"
                 title="${escapeAttr(tip)}">
      <div class="boost-chip-img"><img src="${authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`)}" loading="lazy"></div>
      <span class="boost-chip-name">${esc(label)}</span>
    </div>`;
    })
    .join("");

  const boostSection = document.getElementById("nav-face-boost");
  if (boostSection) {
    boostSection.classList.toggle("has-selection", selectedFaceIds.size > 0);
  }
}

/**
 * @param {number} clusterId
 */
export function toggleFace(clusterId) {
  /** @type {any} */
  const win = window;
  const selectedFaceIds = /** @type {Set<number>} */ (win.selectedFaceIds || new Set());
  if (selectedFaceIds.has(clusterId)) selectedFaceIds.delete(clusterId);
  else selectedFaceIds.add(clusterId);
  _persistBoostSelection(selectedFaceIds);
  renderFaceGallery();
  win.scheduleRecompute?.();
}

export function clearFaceSelection() {
  /** @type {any} */
  const win = window;
  const selectedFaceIds = /** @type {Set<number>} */ (win.selectedFaceIds || new Set());
  selectedFaceIds.clear();
  _persistBoostSelection(selectedFaceIds);
  renderFaceGallery();
  win.scheduleRecompute?.();
}

/**
 * Boost choices are a SETTING, not session state — without persistence
 * every reload silently emptied the selection and the user's "I boosted
 * these people" mental model broke. Stored as a CSV of cluster ids
 * (the settings store stringifies values).
 * @param {Set<number>} ids
 */
function _persistBoostSelection(ids) {
  saveSetting("boosted_cluster_ids", [...ids].join(","));
}

/** Restore the persisted boost selection at boot (app.mjs). */
export function restoreBoostSelection() {
  /** @type {any} */
  const win = window;
  const raw = String(getSetting("boosted_cluster_ids", ""));
  const ids = raw.split(",").map((x) => parseInt(x, 10)).filter((n) => !isNaN(n));
  win.selectedFaceIds = new Set(ids);
}

export async function runAutoOptimize() {
  /** @type {any} */
  const win = window;
  const btn = /** @type {HTMLButtonElement | null} */ (document.getElementById("btn-auto-optimize"));
  const statusEl = document.getElementById("optimize-status");
  if (!btn || !statusEl) return;
  btn.disabled = true;
  btn.textContent = "Optimizing…";
  statusEl.classList.remove("hidden");
  statusEl.textContent = "Sweeping weight combinations…";

  const kInput = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
  const k = parseInt(kInput?.value || "0") || 50;
  /** @type {{k: number, selected_faces?: number[]}} */
  const body = { k };
  const selectedFaceIds = /** @type {Set<number>} */ (win.selectedFaceIds || new Set());
  if (selectedFaceIds.size > 0) body.selected_faces = [...selectedFaceIds];

  try {
    const data = await apiFetch("/api/v1/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    btn.disabled = false;
    btn.textContent = "Auto-optimize weights";

    const b = data.breakdown;
    let statusText = `${(b.avg_quality * 100).toFixed(0)}% avg quality`;
    if (b.face_coverage !== undefined) {
      statusText = `${(b.face_coverage * 100).toFixed(0)}% face coverage, ` + statusText;
    }
    statusEl.textContent = statusText;

    win.applySettings?.(data.settings);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Auto-optimize weights";
    toastError("optimize the weights", e);
  }
}

/** Test-only: reset module-private state. */
export function _resetFacesState() {
  _faceNudgeDismissed = false;
  albumFaceClusterIds = null;
}

// Extraction lifecycle moved to faces-extraction.mjs (LOC gate,
// 2026-06-12). Re-exported so import paths + the window bridge hold.
export {
  _maybeStartClip,
  startFaceExtraction,
  listenFaceProgress,
  retryFaceExtraction,
  confirmRetryFaceExtraction,
} from "./faces-extraction.mjs";
