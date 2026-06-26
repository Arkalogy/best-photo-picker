// @ts-check
/**
 * Scoring-preset CRUD for the toolbar dropdown.
 *
 * A "preset" is a snapshot of slider values + k + selected face IDs that
 * the user can save under a name and re-apply later. Persistence is via
 * the server's `/api/v1/presets` endpoint.
 *
 * Reads `state.selectedFaceIds` / `state.faceClusters` (still classic
 * mutable state) and calls `window.scheduleRecompute` /
 * `window.renderFaceGallery` for now.
 */

import { apiFetch } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appPrompt } from "./dialogs.mjs";
import { formatVal } from "./format-helpers.mjs";
import { showModal } from "./modal.mjs";
import { toastError } from "./toast.mjs";
import { getSensitiveMode, setSensitiveMode } from "./sensitive.mjs";

/**
 * Reload the preset dropdown options from the server, preserving the
 * currently-selected option if it's still there.
 */
export async function loadPresetList() {
  const data = await apiFetch("/api/v1/presets");
  const sel = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("preset-select")
  );
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">Load preset...</option>';
  for (const name of Object.keys(data.presets || {})) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  sel.value = current;
  syncPresetButtons();
}

/**
 * Show / hide the preset-mutation buttons based on whether a preset is
 * currently selected. Run after every change to `#preset-select`.
 */
export function syncPresetButtons() {
  const sel = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("preset-select")
  );
  if (!sel) return;
  const hasPreset = !!sel.value;
  const del = document.getElementById("btn-delete-preset");
  const upd = document.getElementById("btn-update-preset");
  const save = document.getElementById("btn-save-preset");
  if (del) del.style.display = hasPreset ? "inline-block" : "none";
  if (upd) upd.style.display = hasPreset ? "inline-block" : "none";
  if (save) save.textContent = hasPreset ? "Save As…" : "Save";
}

/**
 * Snapshot the current scoring slider values + k + selected faces into a
 * plain object suitable for serialization.
 *
 * @returns {Record<string, any>}
 */
export function getCurrentSettings() {
  /** @type {Record<string, any>} */
  const settings = {};
  document.querySelectorAll("[data-param]").forEach((el) => {
    const e = /** @type {HTMLInputElement} */ (el);
    settings[e.dataset.param] = parseFloat(e.value);
  });
  settings.sensitive_in_picks = getSensitiveMode();
  const kEl = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
  settings.k = (kEl ? parseInt(kEl.value) : NaN) || 50;
  /** @type {any} */
  const win = window;
  if (win.selectedFaceIds && win.selectedFaceIds.size > 0) {
    settings.selected_faces = [...win.selectedFaceIds];
  }
  return settings;
}

/**
 * Restore slider values + k + selected faces from a saved preset and
 * trigger a re-score.
 *
 * @param {Record<string, any>} settings
 */
export function applySettings(settings) {
  document.querySelectorAll("[data-param]").forEach((el) => {
    const e = /** @type {HTMLInputElement} */ (el);
    const key = e.dataset.param;
    if (key !== undefined && settings[key] !== undefined) {
      e.value = settings[key];
      const next = /** @type {HTMLElement | null} */ (e.nextElementSibling);
      if (next) next.textContent = formatVal(key, settings[key]);
    }
  });
  if (settings.sensitive_in_picks !== undefined) {
    setSensitiveMode(settings.sensitive_in_picks);
  }
  if (settings.k !== undefined) {
    const k = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
    if (k) k.value = String(settings.k);
  }
  /** @type {any} */
  const win = window;
  if (settings.selected_faces) {
    win.selectedFaceIds = new Set(settings.selected_faces);
    if (win.faceClusters && win.faceClusters.length > 0) {
      win.renderFaceGallery?.();
    }
  }
  win.scheduleRecompute?.();
}

/**
 * Prompt the user for a name and POST the current settings as a new
 * preset. After saving, refreshes the list and selects the new entry.
 */
export async function showPresetSaveForm() {
  const name = await appPrompt("Save preset", {
    placeholder: "Preset name",
    okLabel: "Save",
  });
  if (!name) return;
  const settings = getCurrentSettings();
  try {
    await apiFetch("/api/v1/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, settings }),
    });
  } catch (e) {
    toastError("save the preset", e);
    return;
  }
  await loadPresetList();
  const sel = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("preset-select")
  );
  if (sel) sel.value = name;
  syncPresetButtons();
}

/**
 * Overwrite the currently-selected preset with the current settings.
 * No-op if nothing is selected.
 */
export async function updatePreset() {
  const sel = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("preset-select")
  );
  const name = sel?.value;
  if (!name) return;
  const settings = getCurrentSettings();
  try {
    await apiFetch("/api/v1/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, settings }),
    });
  } catch (e) {
    toastError("update the preset", e);
    return;
  }
  await loadPresetList();
  if (sel) sel.value = name;
  syncPresetButtons();
}

/**
 * Apply the currently-selected preset's settings. Called from the
 * dropdown's onchange.
 */
export async function loadPreset() {
  const sel = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("preset-select")
  );
  const name = sel?.value;
  syncPresetButtons();
  if (!name) return;
  let data;
  try {
    data = await apiFetch("/api/v1/presets");
  } catch (e) {
    toastError("load the preset", e);
    return;
  }
  const settings = (data.presets || {})[name];
  if (settings) applySettings(settings);
}

/**
 * Confirm + DELETE the currently-selected preset, then refresh the
 * dropdown and clear the selection.
 */
export async function deletePreset() {
  const sel = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("preset-select")
  );
  const name = sel?.value;
  if (!name) return;
  const confirmed = await showModal(
    "\u{1F5D1}",
    "Delete Preset",
    `Are you sure you want to delete "${name}"? This can't be undone.`,
    { confirm: "Delete", danger: true },
  );
  if (!confirmed) return;
  try {
    await apiFetch(`/api/v1/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (e) {
    toastError(`delete the preset "${name}"`, e);
    return;
  }
  if (sel) sel.value = "";
  await loadPresetList();
  syncPresetButtons();
}
