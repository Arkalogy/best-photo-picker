// @ts-check
/**
 * One-time onboarding wizard:
 *  - Step 1: pick the people you want featured (face chips)
 *  - Step 2: server runs /api/optimize, applies the optimized
 *    weights, and lets the user save them as a preset
 *
 * Reads `state.faceRecognitionAvailable` / `state.faceClusters` /
 * `state.selectedFaceIds` (still classic state) and calls
 * `window.renderFaceGallery` once selections are confirmed.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appPrompt as _appPrompt } from "./dialogs.mjs";  // unused — re-export safety
import { applySettings, showPresetSaveForm } from "./presets.mjs";
import { esc, shortCount } from "./text-format.mjs";
import { getSetting, saveSetting } from "./settings-client.mjs";
import { removeNudge, showNudge } from "./nudges.mjs";
import { toastError } from "./toast.mjs";

void _appPrompt; // keep the import resolved for tooling reasons

/** @type {Set<number>} */
let wizardFaceIds = new Set();

/** @returns {Set<number>} */
export function _getWizardFaceIds() {
  return wizardFaceIds;
}

/** @param {Set<number>} s */
export function _setWizardFaceIds(s) {
  wizardFaceIds = s;
}

/**
 * True iff face recognition is available, clusters exist, and the user
 * hasn't already finished the wizard.
 */
export function shouldShowWizard() {
  /** @type {any} */
  const win = window;
  if (!win.faceRecognitionAvailable) return false;
  if (!win.faceClusters || win.faceClusters.length === 0) return false;
  return getSetting("wizard_done", null) !== "true";
}

export function markWizardDone() {
  saveSetting("wizard_done", "true");
}

export function showWizard() {
  wizardFaceIds = new Set();
  wizardStep1();
  document.getElementById("wizard-overlay")?.classList.add("visible");
}

export function closeWizard() {
  document.getElementById("wizard-overlay")?.classList.remove("visible");
  markWizardDone();
  removeNudge("pick_people");
  showNudge("export_ready", "nudge-container");
}

/** Render Step 1 — face-picker chips. */
export function wizardStep1() {
  const icon = document.getElementById("wiz-icon");
  const title = document.getElementById("wiz-title");
  const body = document.getElementById("wiz-body");
  const actions = document.getElementById("wiz-actions");
  if (!icon || !title || !body || !actions) return;

  icon.textContent = "\u{1F464}";
  title.textContent = "Who matters most?";

  /** @type {any} */
  const win = window;
  const clusters = /** @type {any[]} */ (win.faceClusters || []);

  let chipsHtml = '<div class="wiz-faces">';
  for (const c of clusters.slice(0, 16)) {
    const rep = c.representative;
    chipsHtml += `<div class="face-chip" id="wiz-chip-${c.cluster_id}"
      data-action="wizToggleFace" data-arg0="${c.cluster_id}" title="${c.photo_count} photos">
      <img src="${authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`)}" loading="lazy">
      <span class="face-count">${shortCount(c.photo_count)}</span>
    </div>`;
  }
  chipsHtml += "</div>";

  body.innerHTML =
    '<div class="wiz-instruction">Select the people you want featured in your photo selection.</div>' +
    chipsHtml;
  actions.innerHTML = `
    <button class="modal-btn modal-btn-secondary" data-action="closeWizard">Skip</button>
    <button class="modal-btn modal-btn-primary" id="wiz-next1" data-action="wizardStep2" disabled>Next</button>
  `;
}

/**
 * Toggle a face cluster's membership in the wizard's picked-set, and
 * sync the chip's `.selected` class + the Next button's disabled state.
 *
 * @param {number} cid
 */
export function wizToggleFace(cid) {
  if (wizardFaceIds.has(cid)) wizardFaceIds.delete(cid);
  else wizardFaceIds.add(cid);

  /** @type {any} */
  const win = window;
  const clusters = /** @type {any[]} */ (win.faceClusters || []);
  for (const c of clusters) {
    const chip = document.getElementById("wiz-chip-" + c.cluster_id);
    if (chip) {
      chip.classList.toggle("selected", wizardFaceIds.has(c.cluster_id));
    }
  }
  const btn = /** @type {HTMLButtonElement | null} */ (document.getElementById("wiz-next1"));
  if (btn) btn.disabled = wizardFaceIds.size === 0;
}

/** Render Step 2 — apply selections, call /api/optimize, render result. */
export async function wizardStep2() {
  /** @type {any} */
  const win = window;
  win.selectedFaceIds = new Set(wizardFaceIds);
  win.renderFaceGallery?.();

  const icon = document.getElementById("wiz-icon");
  const title = document.getElementById("wiz-title");
  const body = document.getElementById("wiz-body");
  const actions = document.getElementById("wiz-actions");
  if (!icon || !title || !body || !actions) return;

  icon.textContent = "⚙️";
  title.textContent = "Optimizing…";
  body.innerHTML =
    '<div class="wiz-instruction">Finding the best weight combination. This takes a few seconds.</div>';
  actions.innerHTML = "";

  const kEl = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
  const k = (kEl ? parseInt(kEl.value) : NaN) || 50;
  /** @type {Record<string, any>} */
  const reqBody = { k };
  if (wizardFaceIds.size > 0) reqBody.selected_faces = [...wizardFaceIds];

  try {
    const data = await apiFetch("/api/v1/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reqBody),
    });

    const b = data.breakdown;
    icon.textContent = "✨";
    title.textContent = "Optimized!";
    let resultHtml = `<div class="wiz-result">`;
    if (b.face_coverage !== undefined) {
      resultHtml += `<strong>${(b.face_coverage * 100).toFixed(0)}%</strong> of selected photos contain your people<br>`;
    }
    resultHtml += `<strong>${(b.avg_quality * 100).toFixed(0)}%</strong> average photo quality<br>`;
    if (b.face_photos_selected !== undefined) {
      resultHtml += `<strong>${b.face_photos_selected}</strong> of ${b.total_selected} selected photos have faces`;
    } else {
      resultHtml += `<strong>${b.total_selected}</strong> photos selected`;
    }
    resultHtml += `</div>`;
    body.innerHTML =
      resultHtml +
      '<div class="wiz-result-hint">These settings will be applied. You can save them as a preset.</div>';
    actions.innerHTML = `
    <button class="modal-btn modal-btn-secondary" data-action="closeWizard">Done</button>
    <button class="modal-btn modal-btn-primary" data-action="wizSavePreset">Save as Preset</button>
  `;

    applySettings(data.settings);
  } catch (e) {
    title.textContent = "Optimization failed";
    body.innerHTML = `<div class="wiz-error">${esc(/** @type {any} */ (e).message || "Something went wrong")}</div>`;
    actions.innerHTML =
      '<button class="modal-btn modal-btn-primary" data-action="closeWizard">Close</button>';
    toastError("optimize your settings", e);
  }
}

export function wizSavePreset() {
  closeWizard();
  showPresetSaveForm();
}

/** Trigger the wizard 500ms after caller (gives the rest of init time). */
export function maybeShowWizard() {
  if (shouldShowWizard()) {
    setTimeout(showWizard, 500);
  }
}
