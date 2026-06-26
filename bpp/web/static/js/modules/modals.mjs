// @ts-check
/**
 * Settings + Import + Export modals, the model-management list inside
 * Settings (incl. install-package and redownload flows), and the
 * advanced-tab slider hydration. Reads/writes shared globals
 * (`currentGridItems`, `selectedPaths`, `multiSelected`) via `window`.
 */

import { apiFetch, authEventSource } from "./api-client.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { _formatBytes, parseSSE } from "./format-helpers.mjs";
import { appConfirm } from "./dialogs.mjs";
import { getSetting, saveSetting } from "./settings-client.mjs";
import { removeNudge } from "./nudges.mjs";
import { _onSensitiveThresholdInput } from "./sensitive.mjs";
import { toast, toastError } from "./toast.mjs";
import { toggleExportQuality } from "./utils.mjs";

/**
 * Populate the #clear-photo-count warning in Settings → Danger zone
 * so the user sees the actual blast radius BEFORE typing 'delete'.
 * Pure UI surface — no side effects. Failures degrade silently.
 */
export async function _populateClearPhotoCount() {
  const el = document.getElementById("clear-photo-count");
  if (!el) return;
  // Clear stale content before the fetch so a slow API doesn't leave
  // the previous library's count visible during the lookup.
  el.textContent = "";
  try {
    const data = await apiFetch("/api/v1/photos?limit=1");
    const total = (data && (data.total ?? data.count)) || 0;
    if (total > 0) {
      el.textContent =
        `⚠️ You are about to permanently delete ${total.toLocaleString()} ` +
        (total === 1 ? "photo" : "photos") +
        " from disk. This cannot be undone.";
    }
  } catch {
    // Fail-soft: leave the slot empty rather than blocking the modal.
    // The 'type delete to confirm' guard is still in place.
  }
}

export function showImportModal() {
  document.getElementById("import-modal-overlay")?.classList.add("visible");
  const input = /** @type {HTMLInputElement | null} */ (
    document.getElementById("import-dir-input")
  );
  if (input) input.focus();
}

export function hideImportModal() {
  document.getElementById("import-modal-overlay")?.classList.remove("visible");
}

export function validateImportModal() {
  const inp = /** @type {HTMLInputElement | null} */ (
    document.getElementById("import-dir-input")
  );
  const val = (inp?.value || "").trim();
  const btnAnalyze = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-modal-analyze")
  );
  if (btnAnalyze) btnAnalyze.disabled = !val;
  const btnImport = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-modal-import")
  );
  if (btnImport) btnImport.disabled = !val;
}

export async function openImportBrowser() {
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (data.path) {
      const inp = /** @type {HTMLInputElement | null} */ (
        document.getElementById("import-dir-input")
      );
      if (inp) inp.value = data.path;
      validateImportModal();
    }
  } catch (e) {
    toastError("open the folder browser", e);
  }
}

export function startAnalyzeFromModal() {
  /** @type {any} */
  const win = window;
  const inp = /** @type {HTMLInputElement | null} */ (
    document.getElementById("import-dir-input")
  );
  const dir = (inp?.value || "").trim();
  if (!dir) return;
  const legacy = /** @type {HTMLInputElement | null} */ (document.getElementById("input-dir"));
  if (legacy) legacy.value = dir;
  hideImportModal();
  win.startAnalyze?.();
}

export function startImportFromModal() {
  /** @type {any} */
  const win = window;
  const inp = /** @type {HTMLInputElement | null} */ (
    document.getElementById("import-dir-input")
  );
  const dir = (inp?.value || "").trim();
  if (!dir) return;
  const legacy = /** @type {HTMLInputElement | null} */ (document.getElementById("input-dir"));
  if (legacy) legacy.value = dir;
  // Propagate the Live Photo sidecar opt-in to startImport()
  const sidecarToggle = /** @type {HTMLInputElement | null} */ (
    document.getElementById("import-live-photo-sidecars")
  );
  win.importLivePhotoSidecars = sidecarToggle?.checked ?? false;
  hideImportModal();
  win.startImport?.();
}

export function showSettings() {
  /** @type {any} */
  const win = window;
  document.getElementById("settings-overlay")?.classList.add("visible");
  const libName = document.getElementById("library-name-display");
  const settingsLib = document.getElementById("settings-library-name");
  if (settingsLib && libName) settingsLib.textContent = libName.textContent;
  _updateSettingsLibraryBanner();
  const currentTheme = localStorage.getItem("bpp-theme") || "dark";
  document.querySelectorAll(".theme-btn").forEach((btn) => {
    const elH = /** @type {HTMLElement} */ (btn);
    btn.classList.toggle("active", elH.dataset.theme === currentTheme);
  });
  const updToggle = /** @type {HTMLInputElement | null} */ (
    document.getElementById("check-updates-toggle")
  );
  if (updToggle) updToggle.checked = localStorage.getItem("bpp_check_updates") !== "false";
  // M-S4 — Danger zone: fetch the library photo count so the wipe
  // confirmation surfaces the actual blast radius BEFORE the user
  // types 'delete'. Today the toast tells them the count post-delete;
  // by then it's too late. Fail-soft: if the count fetch errors,
  // leave the slot empty rather than blocking the modal.
  _populateClearPhotoCount();

  /** @param {string} sliderId @param {string} key @param {string} dflt @param {(v: string) => void} updater */
  const hydrate = (sliderId, key, dflt, updater) => {
    const slider = /** @type {HTMLInputElement | null} */ (document.getElementById(sliderId));
    const val = String(getSetting(key, dflt));
    if (slider) {
      slider.value = val;
      updater(val);
    }
  };
  hydrate("face-confidence-slider", "face_detection_confidence", "0.20", updateFaceConfidenceLabel);
  hydrate("pet-confidence-slider", "pet_detection_confidence", "0.20", updatePetConfidenceLabel);
  hydrate("group-min-photos-slider", "group_min_photos", "3", updateGroupMinPhotosLabel);
  hydrate("face-embed-conf-slider", "face_embedding_confidence", "0.65", updateFaceEmbedConfLabel);
  hydrate("min-face-area-slider", "min_face_area_pct", "0.20", updateMinFaceAreaLabel);
  hydrate(
    "min-embed-quality-slider",
    "min_embedding_quality",
    "0.25",
    updateMinEmbedQualityLabel
  );
  hydrate("max-long-side-slider", "max_long_side", "1024", updateMaxLongSideLabel);
  hydrate(
    "clip-threshold-slider",
    "clip_similarity_threshold",
    "0.92",
    updateClipThresholdLabel
  );
  hydrate("thumbnail-size-slider", "thumbnail_size", "64", updateThumbnailSizeLabel);
  hydrate(
    "sensitive-threshold-slider",
    "sensitive_nudity_threshold",
    "0.70",
    _onSensitiveThresholdInput
  );

  const symlinkToggle = /** @type {HTMLInputElement | null} */ (
    document.getElementById("follow-symlinks-toggle")
  );
  if (symlinkToggle) symlinkToggle.checked = getSetting("follow_symlinks", "false") === "true";
  loadModelsList();
  win.updateOverrideStats?.();
  win.loadPresetList?.();
}

export function hideSettings() {
  document.getElementById("settings-overlay")?.classList.remove("visible");
}

/**
 * @param {string} tab
 */
export function switchSettingsTab(tab) {
  document.querySelectorAll(".settings-tab").forEach((t) => {
    const elH = /** @type {HTMLElement} */ (t);
    t.classList.toggle("active", elH.dataset.tab === tab);
  });
  document.querySelectorAll(".settings-tab-pane").forEach((p) => {
    p.classList.toggle("active", p.id === "settings-pane-" + tab);
  });
  _updateSettingsLibraryBanner();
  // Force a fresh fetch when the user opens the Activity tab — the
  // 30s poll otherwise leaves entries up to half a minute stale, so a
  // user who just clicked Install / Uninstall sees nothing.
  if (tab === "activity") {
    /** @type {any} */ (window)._fetchActivityEntries?.();
  }
  // Re-render the model registry picker whenever the Models tab
  // opens so install state, use-context, and active-embedder are
  // fresh. The Models tab lives outside the legacy ML-Models flow
  // so loadModelsList() doesn't reach it.
  if (tab === "models") {
    /** @type {any} */ (window).loadFaceEmbedderPicker?.();
  }
}

function _updateSettingsLibraryBanner() {
  const banner = /** @type {HTMLElement | null} */ (
    document.getElementById("settings-library-banner")
  );
  if (!banner) return;
  const activeTab = /** @type {HTMLElement | null} */ (document.querySelector(".settings-tab.active"));
  const tab = activeTab ? activeTab.dataset.tab : "app";
  // The Models tab is mostly app-wide (installed weights, the use-context
  // declaration, and legal acceptances are all global, not per-library —
  // only the *active* model is per-library), so the "Settings for library X"
  // banner would be misleading there. The tab carries its own scope note.
  const libraryScopedTabs = tab !== "app" && tab !== "activity" && tab !== "models";
  banner.style.display = libraryScopedTabs ? "block" : "none";
}

/** @param {string | number} val */
export function updateFaceConfidenceLabel(val) {
  const el = document.getElementById("face-confidence-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2);
  saveSetting("face_detection_confidence", val);
}
/** @param {string | number} val */
export function updatePetConfidenceLabel(val) {
  const el = document.getElementById("pet-confidence-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2);
  saveSetting("pet_detection_confidence", val);
}
/** @param {string | number} val */
export function updateGroupMinPhotosLabel(val) {
  const el = document.getElementById("group-min-photos-val");
  if (el) el.textContent = String(parseInt(String(val), 10));
  saveSetting("group_min_photos", val);
}
/** @param {string | number} val */
export function updateFaceEmbedConfLabel(val) {
  const el = document.getElementById("face-embed-conf-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2);
  saveSetting("face_embedding_confidence", val);
}
/** @param {string | number} val */
export function updateMinFaceAreaLabel(val) {
  const el = document.getElementById("min-face-area-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2) + "%";
  saveSetting("min_face_area_pct", val);
}
/** @param {string | number} val */
export function updateMinEmbedQualityLabel(val) {
  const el = document.getElementById("min-embed-quality-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2);
  saveSetting("min_embedding_quality", val);
}
/** @param {string | number} val */
export function updateMaxLongSideLabel(val) {
  const el = document.getElementById("max-long-side-val");
  if (el) el.textContent = parseInt(String(val)) + " px";
  saveSetting("max_long_side", val);
}
/** @param {string | number} val */
export function updateClipThresholdLabel(val) {
  const el = document.getElementById("clip-threshold-val");
  if (el) el.textContent = parseFloat(String(val)).toFixed(2);
  saveSetting("clip_similarity_threshold", val);
}
/** @param {string | number} val */
export function updateThumbnailSizeLabel(val) {
  const el = document.getElementById("thumbnail-size-val");
  if (el) el.textContent = parseInt(String(val)) + " px";
  saveSetting("thumbnail_size", val);
}
/** @param {boolean} checked */
export function toggleFollowSymlinks(checked) {
  saveSetting("follow_symlinks", checked ? "true" : "false");
}


/** @type {Set<string> | null} */
let _exportBatchPaths = null;

/**
 * Fill the export "Copy method" dropdown from ExportModeRegistry so
 * plugin-registered modes (and hardlink/symlink, which the UI never
 * exposed) are selectable without a frontend edit. Best-effort: on
 * failure the static "copy" fallback option in the HTML stands.
 */
export async function populateExportModes() {
  const sel = /** @type {HTMLSelectElement | null} */ (document.getElementById("export-mode"));
  if (!sel) return;
  const prev = sel.value || "copy";
  try {
    const data = await apiFetch("/api/v1/export/modes");
    const modes = /** @type {{name:string,description:string}[]} */ (data.modes || []);
    if (!modes.length) return;
    sel.innerHTML = modes
      .map(
        (m) =>
          `<option value="${escapeAttr(m.name)}">${esc(m.description || m.name)}</option>`
      )
      .join("");
    // Restore prior selection if still present, else default to copy.
    sel.value = [...sel.options].some((o) => o.value === prev) ? prev : "copy";
  } catch {
    // Leave the static copy fallback; export still works (server defaults to copy).
  }
}

export function showExportModal() {
  document.getElementById("export-modal-overlay")?.classList.add("visible");
  populateExportModes();
  const status = document.getElementById("export-status");
  if (status) {
    status.textContent = "";
    status.className = "export-status export-status-center";
  }
  // Reset post-success UI from the previous open: the Export button is
  // hidden + 'Cancel' is renamed to 'Done' after a successful export.
  // On reopen, the user is starting a fresh export so we restore both.
  const exportBtn = document.getElementById("btn-do-export");
  if (exportBtn instanceof HTMLElement) {
    // Project UI rule: never set style.display = "" — that drops the
    // inline value and falls back to whatever CSS says. The button's
    // parent is a .modal-flex-row (flex container); 'block' here just
    // means 'not display:none', which is what we want post-reset.
    exportBtn.style.display = "block";
  }
  const cancelBtn = document.querySelector(
    '#export-modal-overlay [data-action="hideExportModal"]'
  );
  if (cancelBtn) cancelBtn.textContent = "Cancel";
  // Disable Export until #export-dir has a value — used to fire on
  // empty input and toast 'Enter an output folder path', which read as
  // a broken button. Sync once now, and again on every input.
  _syncExportBtnEnabled();
  const dirInput = document.getElementById("export-dir");
  if (dirInput) {
    dirInput.removeEventListener("input", _syncExportBtnEnabled);
    dirInput.addEventListener("input", _syncExportBtnEnabled);
  }
  // Esc closes the modal — same capture-phase pattern dialogs.mjs uses
  // so it doesn't bubble into the lightbox / global keydown handlers.
  document.addEventListener("keydown", _onExportModalKey, true);
  toggleExportQuality();
  removeNudge("export_ready");
  const scopeEl = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("export-scope")
  );
  if (scopeEl) {
    if (_exportBatchPaths) {
      scopeEl.value = "batch";
      scopeEl.disabled = true;
    } else {
      scopeEl.value = "picks";
      scopeEl.disabled = false;
    }
    // UAT Bug #13: hide the entire 'What to export' field when in
    // batch mode — there's only one option ('Selected photos'),
    // showing it as a disabled dropdown reads as broken UI when in
    // reality the choice is forced by how the user opened the modal.
    // The modal title already says 'Export N Photos' so the context
    // isn't lost.
    const scopeField = scopeEl.closest(".field");
    if (scopeField instanceof HTMLElement) {
      // Project UI rule: never set style.display="" (would drop the
      // inline value and fall back to whatever CSS says). 'block'
      // matches the .field class default; safe in the flex modal body.
      scopeField.style.display = _exportBatchPaths ? "none" : "block";
    }
  }
  updateExportScope();
  const input = /** @type {HTMLInputElement | null} */ (document.getElementById("export-dir"));
  if (input) input.focus();
}

export function hideExportModal() {
  document.getElementById("export-modal-overlay")?.classList.remove("visible");
  document.removeEventListener("keydown", _onExportModalKey, true);
  _exportBatchPaths = null;
}

/** Disable the Export button when the output-folder input is empty, so
 *  the user can't click into an inevitable 'Enter an output folder
 *  path' toast. Re-enables as soon as anything is typed. */
function _syncExportBtnEnabled() {
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-do-export")
  );
  const dirInput = /** @type {HTMLInputElement | null} */ (
    document.getElementById("export-dir")
  );
  if (!btn || !dirInput) return;
  btn.disabled = !dirInput.value.trim();
}

/** @param {KeyboardEvent} e */
function _onExportModalKey(e) {
  if (e.key !== "Escape") return;
  // Ignore if the modal isn't actually visible (defensive).
  const overlay = document.getElementById("export-modal-overlay");
  if (!overlay?.classList.contains("visible")) return;
  e.stopPropagation();
  e.stopImmediatePropagation();
  hideExportModal();
}

export function getExportPaths() {
  /** @type {any} */
  const win = window;
  if (_exportBatchPaths) return _exportBatchPaths;
  const scopeEl = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("export-scope")
  );
  const scope = scopeEl?.value;
  if (scope === "view") {
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    return new Set(items.map((p) => p.filepath));
  }
  return /** @type {Set<string>} */ (win.selectedPaths || new Set());
}

export function updateExportScope() {
  const paths = getExportPaths();
  const count = paths.size;
  const title = document.getElementById("export-modal-title");
  if (!title) return;
  const suffix = count === 1 ? "" : "s";
  title.textContent = count > 0 ? `Export ${count} Photo${suffix}` : "Export Photos";
}

export function batchExport() {
  /** @type {any} */
  const win = window;
  const ms = /** @type {Set<string>} */ (win.multiSelected || new Set());
  // Silent guard: the batch bar (which hosts the Export button) is only
  // .visible when multiSelected.size > 0 (see photos-select.updateMultiSelectUI),
  // so this is unreachable with an empty selection — the hidden bar is the
  // disabled affordance, no toast. (Toast-noise audit item 10.)
  if (!ms.size) return;
  _exportBatchPaths = new Set(ms);
  showExportModal();
}

/** Test-only: read internal _exportBatchPaths. */
export function _getExportBatchPaths() {
  return _exportBatchPaths;
}

/** Test-only: reset module-private state. */
export function _resetModalsState() {
  _exportBatchPaths = null;
}


import {
  installPackage,
  loadModelsList,
  redownloadFeature,
  toggleModel,
  uninstallFeature,
} from "./modals-models.mjs";
export {
  installPackage,
  loadModelsList,
  redownloadFeature,
  toggleModel,
  uninstallFeature,
};
