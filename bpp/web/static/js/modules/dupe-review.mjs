// @ts-check
/**
 * Duplicate review flow — step through duplicate groups, keep best
 * or delete duplicates. Integrates with compare.js by wrapping
 * `_siblingUseThis` / `_siblingDelete` for auto-advance.
 *
 * State (`_dupeGroups`, `_dupeIndex`) is module-internal; classic
 * compare.js reads it via `_getDupeGroups()` / `_getDupeIndex()`.
 */

import { apiFetch } from "./api-client.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * @typedef {Object} DupePhoto
 * @property {string} filepath
 * @property {string} thumb_hash
 * @property {number} aggregate_score
 * @property {number} [blur_score]
 * @property {number} [exposure_score]
 * @property {number} [face_score]
 * @property {number} [composition_score]
 * @property {string} [date_day]
 * @property {string} [original_filename]
 */

/**
 * @typedef {Object} DupeGroup
 * @property {DupePhoto[]} photos - Sorted by score descending; photos[0] is the "best".
 */

/** @type {DupeGroup[] | null} */
let _dupeGroups = null;
let _dupeIndex = 0;
let _dupeDeleted = 0;
let _dupeReviewed = 0;

/** @type {((...args:any[]) => Promise<void>) | null} */
let _origSiblingUseThis = null;
/** @type {((...args:any[]) => Promise<void>) | null} */
let _origSiblingDelete = null;

/** @returns {DupeGroup[] | null} */
export function _getDupeGroups() {
  return _dupeGroups;
}

/** @returns {number} */
export function _getDupeIndex() {
  return _dupeIndex;
}

/** Test-only state reset. */
export function _resetDupeState() {
  _dupeGroups = null;
  _dupeIndex = 0;
  _dupeDeleted = 0;
  _dupeReviewed = 0;
  _origSiblingUseThis = null;
  _origSiblingDelete = null;
}

/**
 * Kick off the review flow. Loads the groups, opens the compare
 * view on the first one, and wraps the compare actions for
 * auto-advance.
 */
export async function startDupeReview() {
  try {
    const data = await apiFetch("/api/v1/duplicates/groups");
    _dupeGroups = data.groups || [];
    if (!_dupeGroups || _dupeGroups.length === 0) {
      _dupeGroups = null;
      toast("No duplicate groups to review");
      return;
    }
    _dupeIndex = 0;
    _dupeDeleted = 0;
    _dupeReviewed = 0;
    _showDupeGroup(_dupeIndex);
  } catch (e) {
    toastError("load duplicate groups", e);
  }
}

/**
 * Open the compare view on the group at `idx`, building the parent
 * + similar-photos shape compare.js expects. Wraps compare actions
 * on first call.
 *
 * @param {number} idx
 */
export function _showDupeGroup(idx) {
  if (!_dupeGroups || idx >= _dupeGroups.length) {
    _endDupeReview();
    return;
  }
  const group = _dupeGroups[idx];
  const best = group.photos[0];
  const others = group.photos.slice(1);

  /** @type {any} */
  const parent = {
    filepath: best.filepath,
    thumb_hash: best.thumb_hash,
    aggregate_score: best.aggregate_score,
    blur_score: best.blur_score,
    exposure_score: best.exposure_score,
    face_score: best.face_score,
    composition_score: best.composition_score,
    date_day: best.date_day,
    original_filename: best.original_filename,
    similar_photos: others.map((o) => ({
      filepath: o.filepath,
      thumb_hash: o.thumb_hash,
      similarity: 1.0,
      aggregate_score: o.aggregate_score,
      blur_score: o.blur_score,
      exposure_score: o.exposure_score,
      face_score: o.face_score,
      composition_score: o.composition_score,
      date_day: o.date_day,
      filename: o.original_filename,
    })),
  };

  /** @type {any} */
  const win = window;
  if (typeof win.openCompareWithSibling === "function") {
    win.openCompareWithSibling(parent, parent.similar_photos, 0);
  }

  // Wrap compare actions for auto-advance — only once per session.
  if (!_origSiblingUseThis && typeof win._siblingUseThis === "function") {
    _origSiblingUseThis = win._siblingUseThis;
    win._siblingUseThis = async () => {
      await _origSiblingUseThis?.();
      if (_dupeGroups) {
        _dupeReviewed++;
        _dupeAdvance();
      }
    };
  }
  if (!_origSiblingDelete && typeof win._siblingDelete === "function") {
    _origSiblingDelete = win._siblingDelete;
    win._siblingDelete = async () => {
      await _origSiblingDelete?.();
      if (_dupeGroups) {
        _dupeDeleted++;
        _dupeReviewed++;
        // Wait until siblings are exhausted before advancing. compare.mjs
        // exposes the live array via `_getCompareSiblings()`.
        const remaining =
          typeof win._getCompareSiblings === "function" ? win._getCompareSiblings() : null;
        if (!remaining || remaining.length === 0) _dupeAdvance();
      }
    };
  }
}

/**
 * Skip the current group. Closes compare, advances index, schedules
 * the next group on a 200ms delay so the close animation finishes.
 */
export function _dupeSkip() {
  _dupeIndex++;
  /** @type {any} */
  const win = window;
  win.closeCompare?.();
  setTimeout(() => _showDupeGroup(_dupeIndex), 200);
}

/**
 * Esc-key handler from compare.js's keydown listener. End the
 * review and close the compare overlay.
 */
export function _dupeSkipOrClose() {
  /** @type {any} */
  const win = window;
  if (!_dupeGroups) {
    win.closeCompare?.();
    return;
  }
  _endDupeReview();
  win.closeCompare?.();
}

/** Auto-advance after Use-this or Delete completes. */
export function _dupeAdvance() {
  _dupeIndex++;
  setTimeout(() => _showDupeGroup(_dupeIndex), 300);
}

/** Restore wrapped compare actions and toast the summary. */
export function _endDupeReview() {
  const reviewed = _dupeReviewed;
  const deleted = _dupeDeleted;

  /** @type {any} */
  const win = window;
  if (_origSiblingUseThis) {
    win._siblingUseThis = _origSiblingUseThis;
    _origSiblingUseThis = null;
  }
  if (_origSiblingDelete) {
    win._siblingDelete = _origSiblingDelete;
    _origSiblingDelete = null;
  }

  _dupeGroups = null;

  if (reviewed === 0) {
    toast("No duplicates to review");
    return;
  }

  const msg =
    deleted > 0
      ? `Reviewed ${reviewed} duplicate group${reviewed !== 1 ? "s" : ""} — deleted ${deleted} photo${deleted !== 1 ? "s" : ""}`
      : `Reviewed ${reviewed} duplicate group${reviewed !== 1 ? "s" : ""}`;
  toast(msg);
}
