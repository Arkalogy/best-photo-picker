// @ts-check
/**
 * Sibling-compare flow inside the compare overlay.
 *
 * Extracted from compare.mjs during the v0.1 cleanup. Owns the
 * "current pick vs. near-duplicate" mode used by dupe-review and CLIP
 * near-duplicate browsing: a parent photo on the left, a strip of
 * sibling candidates the user can step through on the right, with
 * "use this instead" / "delete" actions.
 *
 * Re-exported from compare.mjs.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import {
  _renderCompareSide,
  closeCompare,
  setCompareOpen,
  setCompareSides,
} from "./compare.mjs";

let _compareSiblingMode = false;
/** @type {any[]} */
let _compareSiblings = [];
let _compareSiblingIdx = -1;
/** @type {any} */
let _compareSiblingParent = null;
let _compareSiblingReturnIdx = -1;

export function getSiblings() {
  return _compareSiblings;
}
export function isSiblingMode() {
  return _compareSiblingMode;
}
export function getSiblingReturnIdx() {
  return _compareSiblingReturnIdx;
}
export function getSiblingIdx() {
  return _compareSiblingIdx;
}
export function getSiblingParent() {
  return _compareSiblingParent;
}
export function resetSiblingState() {
  _compareSiblingMode = false;
  _compareSiblings = [];
  _compareSiblingIdx = -1;
  _compareSiblingParent = null;
  _compareSiblingReturnIdx = -1;
}

/**
 * @param {any} parentPhoto
 * @param {any[]} siblings
 * @param {number} siblingIdx
 */
export function openCompareWithSibling(parentPhoto, siblings, siblingIdx) {
  /** @type {any} */
  const win = window;
  if (!parentPhoto || !siblings || !siblings.length) return;
  _compareSiblingMode = true;
  _compareSiblings = siblings;
  _compareSiblingIdx = siblingIdx;
  _compareSiblingParent = parentPhoto;
  _compareSiblingReturnIdx =
    typeof win.lightboxIdx === "number" && win.lightboxIdx >= 0 ? win.lightboxIdx : -1;
  setCompareOpen(true);
  setCompareSides(-1, -1);

  if (typeof win.lightboxIdx === "number" && win.lightboxIdx >= 0) win.closeLightbox?.();

  _renderCompareSide("left", parentPhoto);
  _renderCompareSide("right", siblings[siblingIdx]);
  _renderSiblingLabels();
  _renderSiblingStrip();
  _renderSiblingToolbar();
  document.getElementById("compare-overlay")?.classList.add("visible");
}

/**
 * @param {number} dir
 */
export function _siblingNav(dir) {
  if (!_compareSiblingMode || !_compareSiblings.length) return;
  _compareSiblingIdx += dir;
  if (_compareSiblingIdx < 0) _compareSiblingIdx = _compareSiblings.length - 1;
  if (_compareSiblingIdx >= _compareSiblings.length) _compareSiblingIdx = 0;
  _renderCompareSide("right", _compareSiblings[_compareSiblingIdx]);
  _renderSiblingLabels();
  _renderSiblingStrip();
  _renderSiblingToolbar();
}

export function _renderSiblingLabels() {
  /** @type {any} */
  const win = window;
  const ll = document.getElementById("compare-left-label");
  const rl = document.getElementById("compare-right-label");
  if (!ll || !rl) return;
  if (_compareSiblingMode) {
    const rp = _compareSiblings[_compareSiblingIdx];
    const isDupe = typeof win._getDupeGroups === "function" && win._getDupeGroups() !== null;
    const simLabel = rp?.similarity != null
      ? `${(rp.similarity * 100).toFixed(0)}% match`
      : "similar shot";
    ll.innerHTML = isDupe ? "Best Photo" : "Current Pick";
    ll.className = "compare-side-label label-best";
    rl.innerHTML = isDupe
      ? `Duplicate <span>&middot; ${simLabel}</span>`
      : `Similar Photo <span>&middot; ${simLabel}</span>`;
    rl.className = "compare-side-label label-similar";
  } else {
    ll.textContent = "";
    ll.className = "compare-side-label";
    rl.textContent = "";
    rl.className = "compare-side-label";
  }
}

function _renderSiblingStrip() {
  const strip = document.getElementById("compare-sibling-strip");
  if (!strip) return;
  if (!_compareSiblingMode || !_compareSiblings.length) {
    strip.classList.add("hidden");
    return;
  }
  const parent = _compareSiblingParent;
  let html = `<img class="css-thumb-best" src="${authedSrc("/thumb/" + parent.thumb_hash)}" alt="Current" title="Current pick">`;
  html += '<div class="css-strip-sep"></div>';
  html += _compareSiblings
    .map((s, i) => {
      const active = i === _compareSiblingIdx ? " active" : "";
      // Moment siblings carry no per-pair similarity (null) — show a neutral
      // tooltip instead of "NaN% similar".
      const tip = s.similarity != null ? (s.similarity * 100).toFixed(0) + "% similar" : "Burst shot";
      return `<img class="css-thumb${active}" src="${authedSrc("/thumb/" + s.thumb_hash)}" alt="" title="${escapeAttr(tip)}" data-action="_siblingJump" data-arg0="${i}">`;
    })
    .join("");
  strip.innerHTML = html;
  strip.classList.remove("hidden");
}

/**
 * @param {number} idx
 */
export function _siblingJump(idx) {
  if (!_compareSiblingMode) return;
  _compareSiblingIdx = idx;
  _renderCompareSide("right", _compareSiblings[idx]);
  _renderSiblingLabels();
  _renderSiblingStrip();
  _renderSiblingToolbar();
}

export async function _siblingUseThis() {
  /** @type {any} */
  const win = window;
  if (!_compareSiblingMode) return;
  const sibling = _compareSiblings[_compareSiblingIdx];
  const parent = _compareSiblingParent;
  if (!sibling || !parent) return;
  try {
    if (win.currentAlbumId) {
      await apiFetch(`/api/v1/albums/${win.currentAlbumId}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepath: sibling.filepath, mode: "include" }),
      });
      await apiFetch(`/api/v1/albums/${win.currentAlbumId}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepath: parent.filepath, mode: "exclude" }),
      });
    }
    toast(`Swapped: using ${sibling.filename || "sibling"} instead`);
    closeCompare();
  } catch (e) {
    toastError("swap to this photo", e);
  }
}

/**
 * Mark/unmark a photo trashed in the in-memory `window.photos` and live-
 * update the grid. The Moments stacks view re-collapses on renderGrid:
 * buildMomentStacks skips `deleted_at`, so the burst's count badge ticks
 * down (and the stack vanishes if the burst falls below 2) — and Undo
 * reverses it. No-op when there's no grid (other compare contexts).
 * @param {string} filepath
 * @param {boolean} deleted
 */
function _setPhotoDeletedInMemory(filepath, deleted) {
  /** @type {any} */
  const win = window;
  const p = (win.photos || []).find((x) => x.filepath === filepath);
  if (p) p.deleted_at = deleted ? new Date().toISOString() : null;
  win.renderGrid?.({ keepScroll: true });
}

export async function _siblingDelete() {
  if (!_compareSiblingMode) return;
  const idx = _compareSiblingIdx;
  const sibling = _compareSiblings[idx];
  if (!sibling || !sibling.filepath) return;
  const ok = await appConfirm(`Delete ${sibling.filename || "this photo"}? It will be moved to trash.`);
  if (!ok) return;
  try {
    await apiFetch("/api/v1/photos/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: [sibling.filepath] }),
    });
  } catch (e) {
    toastError("delete that photo", e);
    return;
  }

  // Live-update the grid (count badge / stack vanish) by flagging deleted.
  _setPhotoDeletedInMemory(sibling.filepath, true);

  // Drop from the compare strip.
  _compareSiblings.splice(idx, 1);
  const closedAfter = _compareSiblings.length === 0;
  if (closedAfter) {
    closeCompare();
  } else {
    if (_compareSiblingIdx >= _compareSiblings.length) _compareSiblingIdx = 0;
    _renderCompareSide("right", _compareSiblings[_compareSiblingIdx]);
    _renderSiblingLabels();
    _renderSiblingStrip();
    _renderSiblingToolbar();
  }

  // 20s recoverable Undo (congruent with the Moments prune spec).
  const name = sibling.filename || sibling.filepath.split("/").pop();
  toast(`Moved "${name}" to trash`, undefined, {
    duration: 20000,
    action: { label: "Undo", fn: () => _undoSiblingDelete(sibling, idx, closedAfter) },
  });
}

/**
 * Restore a just-trashed sibling: un-delete server-side + in-memory, and —
 * if the compare strip for this burst is still open — slot it back at its
 * original index so the burst looks untouched.
 * @param {any} sibling
 * @param {number} idx  original index in the strip
 * @param {boolean} wasClosed  compare had auto-closed (last sibling) — restore silently, don't reopen
 */
async function _undoSiblingDelete(sibling, idx, wasClosed) {
  try {
    await apiFetch("/api/v1/photos/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: [sibling.filepath] }),
    });
  } catch (e) {
    toastError("restore that photo", e);
    return;
  }
  _setPhotoDeletedInMemory(sibling.filepath, false);
  if (_compareSiblingMode && !wasClosed) {
    _compareSiblings.splice(Math.min(idx, _compareSiblings.length), 0, sibling);
    _compareSiblingIdx = idx;
    _renderCompareSide("right", _compareSiblings[_compareSiblingIdx]);
    _renderSiblingLabels();
    _renderSiblingStrip();
    _renderSiblingToolbar();
  }
  toast("Restored");
}

export function _renderSiblingToolbar() {
  /** @type {any} */
  const win = window;
  const toolbar = document.getElementById("compare-toolbar");
  if (!toolbar) return;
  const n = _compareSiblings.length;
  const cur = _compareSiblingIdx + 1;
  const isDupe = typeof win._getDupeGroups === "function" && win._getDupeGroups() !== null;

  let html = `
    <button class="ct-action ct-pick" data-action="_siblingUseThis" title="Use this photo instead">&#10003; Use this instead <kbd>Enter</kbd></button>
    <button class="ct-action ct-del" data-action="_siblingDelete" title="Delete this similar photo">&#10005; Delete <kbd>D</kbd></button>
    <div class="ct-sep"></div>
    <span class="ct-info">${cur} of ${n}</span>`;

  if (n > 1) {
    html += `<div class="ct-sep"></div>
    <button data-action="_siblingNav" data-arg0="-1" title="Previous similar">&lsaquo; Prev <kbd>&larr;</kbd></button>
    <button data-action="_siblingNav" data-arg0="1" title="Next similar">Next &rsaquo; <kbd>&rarr;</kbd></button>`;
  }

  if (isDupe) {
    const gIdx = typeof win._getDupeIndex === "function" ? win._getDupeIndex() : 0;
    const gTotal = (win._getDupeGroups() || []).length;
    html += `<div class="ct-sep"></div>
    <span class="ct-info" style="font-weight:600">Group ${gIdx + 1} of ${gTotal}</span>
    <button data-action="_dupeSkip" title="Skip this group">Skip <kbd>&rarr;</kbd></button>`;
  }

  html += `<div class="ct-sep"></div>
    <span class="ct-info">Click to zoom</span>
    <div class="ct-sep"></div>
    <button data-action="${isDupe ? "_dupeSkipOrClose" : "closeCompare"}" title="Close">Close <kbd>Esc</kbd></button>`;

  toolbar.innerHTML = html;
}
