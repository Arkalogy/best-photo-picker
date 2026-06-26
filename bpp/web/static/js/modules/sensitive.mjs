// @ts-check
/**
 * Sensitive-photo flag: chip rendering, override toggle, and the
 * export review-gate helpers.
 *
 * The verdict (`p.is_sensitive`) is derived SERVER-side in
 * build_photo_dict from the NudeNet score + the user's per-photo
 * override — this module never re-derives it with its own threshold.
 * Copy rules (pm.md spec): neutral language ("may be sensitive",
 * never "NSFW"), always note detection is local-only, and the user
 * can always correct the model.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { saveSetting } from "./settings-client.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * Chip for the lightbox quality panel. Empty string when the photo
 * isn't flagged — the chip is a passive indicator, not a nag.
 * @param {any} p photo dict
 * @returns {string}
 */
export function sensitiveChipHTML(p) {
  if (!p || !p.is_sensitive) return "";
  const why =
    p.sensitive_override === 1
      ? "You marked this photo as sensitive."
      : "The on-device content filter thinks this photo may be sensitive.";
  return (
    '<button class="lb-sensitive-chip" data-action="lbToggleSensitive" ' +
    `title="${why}&#10;Detection runs entirely on this Mac.&#10;Click to mark as not sensitive.">` +
    "&#9888; May be sensitive</button>"
  );
}

/**
 * Context-menu label for the override toggle.
 * @param {any} p photo dict
 * @returns {string}
 */
export function sensitiveCtxLabel(p) {
  return p && p.is_sensitive ? "Not sensitive" : "Mark sensitive";
}

/**
 * Split a list of photo dicts into sensitive / clean for the export
 * review gate. Pure — unit-tested per branch.
 * @param {any[]} items
 * @returns {{flagged: any[], clean: any[]}}
 */
export function partitionSensitive(items) {
  /** @type {any[]} */
  const flagged = [];
  /** @type {any[]} */
  const clean = [];
  for (const p of items || []) {
    (p && p.is_sensitive ? flagged : clean).push(p);
  }
  return { flagged, clean };
}

/**
 * Body HTML for the export review gate: one row per flagged photo
 * with a thumbnail and a checked-by-default "include" checkbox.
 * Pure — unit-tested.
 * @param {any[]} flagged
 * @param {(path: string) => string} thumbSrc maps thumb_hash → src URL
 * @returns {string}
 */
export function sensitiveReviewListHTML(flagged, thumbSrc) {
  return (flagged || [])
    .map(
      (p, i) =>
        '<label class="sensitive-review-row">' +
        `<input type="checkbox" class="sensitive-review-keep" data-idx="${i}" checked>` +
        `<img class="sensitive-review-thumb" src="${thumbSrc(p.thumb_hash)}" alt="">` +
        `<span class="sensitive-review-name">${(p.filename || "").replace(/[<>&"]/g, "")}</span>` +
        "</label>",
    )
    .join("");
}

/**
 * Read the review modal's checkboxes back into a keep-set.
 * @param {any[]} flagged
 * @param {ParentNode} root modal body element
 * @returns {Set<string>} filepaths the user chose to keep in the export
 */
export function readReviewSelections(flagged, root) {
  /** @type {Set<string>} */
  const keep = new Set();
  root.querySelectorAll(".sensitive-review-keep").forEach((el) => {
    const input = /** @type {HTMLInputElement} */ (el);
    const idx = Number(input.dataset.idx);
    if (input.checked && flagged[idx]) keep.add(flagged[idx].filepath);
  });
  return keep;
}

/**
 * Export review gate (P0 of the sensitive-photo spec): when the
 * selection about to be exported contains flagged photos, show a
 * review dialog with per-photo keep/remove checkboxes. The ONE
 * decision point, exactly where accidental sharing happens.
 *
 * Resolves to `{proceed, paths}`:
 *   proceed=false → user cancelled, abort the export untouched;
 *   proceed=true  → paths is the selection minus any photos the user
 *                   unchecked. No flagged photos → passes through
 *                   silently (no modal, no nag).
 * @param {string[]} exportPaths
 * @returns {Promise<{proceed: boolean, paths: string[]}>}
 */
export async function reviewSensitiveBeforeExport(exportPaths) {
  /** @type {any} */
  const win = window;
  const pool = /** @type {any[]} */ (
    (win.photos && win.photos.length ? win.photos : win.currentGridItems) || []
  );
  const byPath = new Map(pool.map((p) => [p.filepath, p]));
  const items = exportPaths.map((fp) => byPath.get(fp)).filter(Boolean);
  const { flagged } = partitionSensitive(items);
  if (flagged.length === 0) return { proceed: true, paths: exportPaths };

  const bodyHTML =
    '<div class="sensitive-review-list">' +
    sensitiveReviewListHTML(flagged, (h) => authedSrc("/thumb/" + h)) +
    "</div>";
  const ok = await appConfirm(
    `${flagged.length} of ${exportPaths.length} photo${exportPaths.length !== 1 ? "s" : ""} may be sensitive`,
    "Uncheck any photo to leave it out of this export. Detection runs entirely on this Mac.",
    { okLabel: "Export", bodyHTML },
  );
  if (!ok) return { proceed: false, paths: exportPaths };

  // The dialog DOM persists after resolve (only the overlay hides),
  // so the checkbox states are still readable here.
  const dialog = document.querySelector(".confirm-dialog");
  const keep = dialog ? readReviewSelections(flagged, dialog) : new Set();
  const removed = new Set(
    flagged.filter((p) => !keep.has(p.filepath)).map((p) => p.filepath),
  );
  return { proceed: true, paths: exportPaths.filter((fp) => !removed.has(fp)) };
}

// ── "Sensitive photos in auto-picks" 2-way control (allow | exclude) ──
//
// This is a STRING enum, so it deliberately does NOT use the
// `[data-param]` slider bus — analysis-recompute.getParams() reads
// those via parseFloat(), which would turn "allow"/"exclude" into NaN.
// Instead this module owns the control's read/write and is wired
// explicitly into the recompute payload (getParams), preset save/
// restore (presets.mjs), and the two config-hydration seams (app.mjs
// boot defaults + albums-switch per-album config).

const SENSITIVE_MODES = ["allow", "exclude"];
const SENSITIVE_DEFAULT = "allow";

/**
 * Read the active mode from the segmented control. Falls back to the
 * default ("allow") when the control is absent or in an unknown state.
 * @returns {"allow" | "exclude"}
 */
export function getSensitiveMode() {
  const active = document.querySelector("#sensitive-toggle .theme-btn.active");
  const mode = active?.getAttribute("data-sens") || "";
  return /** @type {"allow" | "exclude"} */ (
    SENSITIVE_MODES.includes(mode) ? mode : SENSITIVE_DEFAULT
  );
}

/**
 * Reflect `mode` in the segmented control (active class + aria-checked).
 * No-op for unknown values so a stray config string can't blank the
 * control.
 * @param {string} mode
 */
export function setSensitiveMode(mode) {
  if (!SENSITIVE_MODES.includes(mode)) return;
  const ctrl = document.getElementById("sensitive-toggle");
  if (!ctrl) return;
  ctrl.querySelectorAll(".theme-btn").forEach((b) => {
    const on = b.getAttribute("data-sens") === mode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

/**
 * data-action handler for the two buttons. Sets the mode and triggers
 * a recompute so the picks update live (the change is sent on the next
 * recompute payload via getParams).
 * @param {string} mode
 */
export function _setSensitiveMode(mode) {
  if (getSensitiveMode() === mode) return; // no-op re-click
  setSensitiveMode(mode);
  /** @type {any} */ (window).scheduleRecompute?.();
}

// ── "Flag sensitivity" threshold slider (sensitive_nudity_threshold) ──
//
// A library-wide setting (persisted to the DB settings table via
// saveSetting → PUT /api/v1/settings, where Config reads it). NudeNet
// confidence at/above which a photo is flagged "may be sensitive".
// Default 0.70 — raised from 0.50 after baby-photo false positives (see
// SENSITIVE_NUDITY_THRESHOLD in constants.py).

/**
 * Live label update while dragging (fires on `input`, no persistence).
 * @param {string} value
 */
export function _onSensitiveThresholdInput(value) {
  const label = document.getElementById("sensitive-threshold-val");
  if (label) label.textContent = Number(value).toFixed(2);
}

/**
 * Commit on release (fires on `change`): persist the threshold and
 * re-derive the Sensitive smart album so membership reflects it. Already
 * -rendered chips refresh on the next view load.
 * @param {string} value
 */
export async function _onSensitiveThresholdCommit(value) {
  const v = Number(value);
  if (!Number.isFinite(v)) return;
  saveSetting("sensitive_nudity_threshold", v);
  try {
    await apiFetch("/api/v1/albums/refresh-smart", { method: "POST" });
    /** @type {any} */ (window).loadAlbumList?.();
    toast(`Sensitive flag threshold set to ${v.toFixed(2)}`);
  } catch (err) {
    toastError("update the sensitive threshold", err);
  }
}

/**
 * Persist the user's override for one photo.
 * @param {string} filepath
 * @param {0 | 1 | null} override 1 = sensitive, 0 = not, null = follow model
 */
export async function postSensitiveOverride(filepath, override) {
  // Service wrapper, NOT a leaf handler: it lets apiFetch's rejection
  // propagate so the caller (lbToggleSensitive) can skip its optimistic
  // DOM update and toastError once. Toasting here too would double-toast.
  // Listed in ERROR_TOAST_BASELINE as an intentional exception.
  return apiFetch("/api/v1/photos/sensitive", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filepath, override }),
  });
}

/**
 * Navigate to the Sensitive smart album (target of the post-analysis
 * alert's "Review" action). Refreshes the album list first — the album
 * may have just been created by the analyze run.
 */
export async function openSensitiveAlbum() {
  /** @type {any} */
  const win = window;
  try {
    await win.loadAlbumList?.();
    const album = /** @type {any[]} */ (win.albumList || []).find(
      (a) => a.album_type === "smart_sensitive",
    );
    if (album) win.switchAlbum?.(album.id);
    else toast("No photos are flagged as sensitive right now");
  } catch (err) {
    toastError("open the Sensitive album", err);
  }
}

/**
 * Lightbox action: toggle the sensitive flag on the open photo.
 * Wired from the quality-panel chip and the context menu via the
 * data-action bridge.
 */
export async function lbToggleSensitive() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  const target = p.is_sensitive ? 0 : 1;
  try {
    const res = await postSensitiveOverride(p.filepath, target);
    p.sensitive_override = target;
    p.is_sensitive = !!res.is_sensitive;
    win.updateLightboxScores?.(p);
    toast(p.is_sensitive ? "Marked as sensitive" : "Marked as not sensitive");
    // Sensitive album membership just changed — refresh the sidebar.
    win.loadAlbumList?.();
  } catch (err) {
    toastError("update the sensitive flag", err);
  }
}
