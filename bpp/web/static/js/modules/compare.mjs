// @ts-check
/**
 * Side-by-side compare overlay. Two flavours:
 *   1. Free compare: pick any two photos from the grid and toggle
 *      include/exclude overrides.
 *   2. Sibling compare: walk through the `similar_photos` of a parent
 *      photo (used by dupe-review and CLIP near-duplicate browsing).
 *
 * Self-attaches a global keydown listener on import.
 *
 * Reads/writes shared globals on `window` (`currentGridItems`,
 * `lightboxIdx`, `multiSelected`, `currentAlbumId`, `SCORE_LABELS`)
 * and calls cross-file helpers (`closeLightbox`, `openLightbox`)
 * the same way. Dupe-review state is read via `_getDupeGroups()` /
 * `_getDupeIndex()` (window-bridged from dupe-review.mjs).
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { barColor, qualityLabel } from "./score-format.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import {
  _siblingDelete,
  _siblingNav,
  _siblingUseThis,
  getSiblingIdx,
  getSiblingParent,
  getSiblingReturnIdx,
  getSiblings,
  isSiblingMode,
  resetSiblingState,
} from "./compare-sibling.mjs";

let compareOpen = false;
let compareLeft = -1;
let compareRight = -1;

/** Internal setter — used by the sibling submodule when it opens. */
export function setCompareOpen(v) {
  compareOpen = v;
}
/** Internal setter — used by the sibling submodule. */
export function setCompareSides(l, r) {
  compareLeft = l;
  compareRight = r;
}

/** Test surface — read internal sibling list from outside the module. */
export function _getCompareSiblings() {
  return getSiblings();
}

/** Test surface — read internal sibling-mode flag. */
export function _isCompareSiblingMode() {
  return isSiblingMode();
}

/** Test-only: reset module-private state. */
export function _resetCompareState() {
  compareOpen = false;
  compareLeft = -1;
  compareRight = -1;
  resetSiblingState();
}

/**
 * @param {number} leftIdx
 * @param {number} rightIdx
 */
export function openCompare(leftIdx, rightIdx) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (leftIdx < 0 || rightIdx < 0) return;
  if (leftIdx >= items.length || rightIdx >= items.length) return;
  compareLeft = leftIdx;
  compareRight = rightIdx;
  compareOpen = true;
  resetSiblingState();

  if (typeof win.lightboxIdx === "number" && win.lightboxIdx >= 0) win.closeLightbox?.();

  _renderCompareSide("left", items[compareLeft]);
  _renderCompareSide("right", items[compareRight]);
  _renderCompareToolbar();
  document.getElementById("compare-overlay")?.classList.add("visible");
}

/**
 * @param {MouseEvent} [e]
 */
export function closeCompare(e) {
  /** @type {any} */
  const win = window;
  if (e && e.target !== e.currentTarget) return;
  const returnIdx = getSiblingReturnIdx();
  const wasSibling = isSiblingMode();
  document.getElementById("compare-overlay")?.classList.remove("visible");
  document.getElementById("compare-sibling-strip")?.classList.add("hidden");
  const ll = document.getElementById("compare-left-label");
  const rl = document.getElementById("compare-right-label");
  if (ll) {
    ll.textContent = "";
    ll.className = "compare-side-label";
  }
  if (rl) {
    rl.textContent = "";
    rl.className = "compare-side-label";
  }
  compareOpen = false;
  compareLeft = -1;
  compareRight = -1;
  resetSiblingState();
  if (wasSibling && returnIdx >= 0) {
    win.openLightbox?.(returnIdx);
  }
}

export function isCompareOpen() {
  return compareOpen;
}

export function openCompareFromSelection() {
  /** @type {any} */
  const win = window;
  const ms = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const paths = [...ms];
  if (paths.length < 2) {
    toast("Select at least 2 photos to compare", true);
    return;
  }
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const idx1 = items.findIndex((p) => p.filepath === paths[0]);
  const idx2 = items.findIndex((p) => p.filepath === paths[1]);
  if (idx1 < 0 || idx2 < 0) {
    toast("Selected photos not found in grid", true);
    return;
  }
  openCompare(idx1, idx2);
}

/**
 * @param {"left" | "right"} side
 * @param {number} dir
 */
export function compareNav(side, dir) {
  /** @type {any} */
  const win = window;
  if (!compareOpen) return;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const isLeft = side === "left";
  const curIdx = isLeft ? compareLeft : compareRight;
  let newIdx = curIdx + dir;
  if (newIdx < 0) newIdx = items.length - 1;
  if (newIdx >= items.length) newIdx = 0;
  if (isLeft && newIdx === compareRight) newIdx += dir;
  if (!isLeft && newIdx === compareLeft) newIdx += dir;
  if (newIdx < 0) newIdx = items.length - 1;
  if (newIdx >= items.length) newIdx = 0;

  if (isLeft) compareLeft = newIdx;
  else compareRight = newIdx;

  _renderCompareSide(side, items[newIdx]);
  _renderCompareToolbar();
}

export function compareSwap() {
  /** @type {any} */
  const win = window;
  if (!compareOpen) return;
  const tmp = compareLeft;
  compareLeft = compareRight;
  compareRight = tmp;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  _renderCompareSide("left", items[compareLeft]);
  _renderCompareSide("right", items[compareRight]);
}

/**
 * @param {"left" | "right"} side
 */
export async function comparePick(side) {
  /** @type {any} */
  const win = window;
  if (!compareOpen) return;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const winnerIdx = side === "left" ? compareLeft : compareRight;
  const loserIdx = side === "left" ? compareRight : compareLeft;
  const wp = items[winnerIdx];
  const lp = items[loserIdx];

  try {
    if (win.currentAlbumId) {
      await apiFetch(`/api/v1/albums/${win.currentAlbumId}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepath: wp.filepath, mode: "include" }),
      });
      await apiFetch(`/api/v1/albums/${win.currentAlbumId}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepath: lp.filepath, mode: "exclude" }),
      });
    }

    const fname = wp.filename || wp.filepath.split("/").pop();
    toast(`Picked ${fname}`);

    const loserSide = side === "left" ? "right" : "left";
    compareNav(loserSide, 1);
  } catch (e) {
    toastError("pick this photo", e);
  }
}

/**
 * @param {MouseEvent} e
 */
export function _compareZoom(e) {
  const img = /** @type {HTMLElement} */ (e.currentTarget);
  if (img.classList.contains("zoomed")) {
    img.classList.remove("zoomed");
    return;
  }
  const rect = img.getBoundingClientRect();
  const x = (((e.clientX - rect.left) / rect.width) * 100).toFixed(1);
  const y = (((e.clientY - rect.top) / rect.height) * 100).toFixed(1);
  img.style.transformOrigin = `${x}% ${y}%`;
  img.classList.add("zoomed");
}

/**
 * @param {"left" | "right"} side
 * @param {any} photo
 */
export function _renderCompareSide(side, photo) {
  /** @type {any} */
  const win = window;
  const img = /** @type {HTMLImageElement | null} */ (
    document.getElementById(`compare-${side}-img`)
  );
  if (img) {
    img.src = authedSrc("/photo/" + photo.thumb_hash);
    img.classList.remove("zoomed");
    img.onclick = _compareZoom;
  }

  const info = document.getElementById(`compare-${side}-info`);
  if (!info) return;
  const fname = photo.filename || photo.filepath.split("/").pop();
  const q = qualityLabel(photo.aggregate_score);
  const agg = Math.round((photo.aggregate_score || 0) * 100);

  const scores = [
    { key: "blur_score", v: photo.blur_score || 0 },
    { key: "exposure_score", v: photo.exposure_score || 0 },
    { key: "face_score", v: photo.face_score || 0 },
    { key: "composition_score", v: photo.composition_score || 0 },
  ];

  let other;
  if (isSiblingMode()) {
    const sibs = getSiblings();
    const idx = getSiblingIdx();
    other = side === "left" ? sibs[idx] : getSiblingParent();
  } else {
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    const otherIdx = side === "left" ? compareRight : compareLeft;
    other = items[otherIdx];
  }

  /** @type {Record<string, string>} */
  const labels = win.SCORE_LABELS || {
    blur_score: "Sharpness",
    exposure_score: "Exposure",
    face_score: "Faces",
    composition_score: "Composition",
  };

  info.innerHTML = `
    <div class="ci-body">
      <div class="ci-agg">
        <span class="ci-agg-val" style="color:${q.color}">${agg}%</span>
        <span class="ci-agg-label">Quality</span>
      </div>
      <div class="ci-right">
        <div class="ci-meta">
          <span class="ci-filename" title="${escapeAttr(fname)}">${esc(fname)}</span>
          <span class="ci-date">${esc(photo.date_day || "")}</span>
        </div>
        <div class="ci-scores">
          ${scores
            .map((s) => {
              const pct = Math.round((s.v || 0) * 100);
              const otherVal = other ? other[s.key] || 0 : 0;
              const isWinner = (s.v || 0) > otherVal;
              return `<div class="ci-score-row">
              <span class="ci-score-label">${labels[s.key] || s.key}</span>
              <div class="ci-score-bar"><div class="ci-score-fill" style="width:${pct}%;background:${barColor(s.v)}"></div></div>
              <span class="ci-score-val">${pct}%${isWinner ? '<span class="ci-winner-mark"> +</span>' : ""}</span>
            </div>`;
            })
            .join("")}
        </div>
      </div>
    </div>
  `;
}

function _renderCompareToolbar() {
  /** @type {any} */
  const win = window;
  const toolbar = document.getElementById("compare-toolbar");
  if (!toolbar) return;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const lp = items[compareLeft];
  const rp = items[compareRight];
  if (!lp || !rp) return;

  toolbar.innerHTML = `
    <button data-action="comparePick" data-arg0="left" title="Pick left photo">Pick Left <kbd>1</kbd></button>
    <button data-action="comparePick" data-arg0="right" title="Pick right photo">Pick Right <kbd>2</kbd></button>
    <div class="ct-sep"></div>
    <button data-action="compareSwap" title="Swap sides">Swap <kbd>S</kbd></button>
    <div class="ct-sep"></div>
    <button data-action="compareNav" data-arg0="left" data-arg1="-1" title="Previous left">&lsaquo; Left <kbd>A</kbd></button>
    <button data-action="compareNav" data-arg0="left" data-arg1="1" title="Next left">Left &rsaquo; <kbd>D</kbd></button>
    <div class="ct-sep"></div>
    <button data-action="compareNav" data-arg0="right" data-arg1="-1" title="Previous right">&lsaquo; Right <kbd>J</kbd></button>
    <button data-action="compareNav" data-arg0="right" data-arg1="1" title="Next right">Right &rsaquo; <kbd>L</kbd></button>
    <div class="ct-sep"></div>
    <button data-action="closeCompare" title="Close compare">Close <kbd>Esc</kbd></button>
  `;
}

document.addEventListener("keydown", (e) => {
  /** @type {any} */
  const win = window;
  if (!compareOpen) return;
  const target = /** @type {HTMLElement} */ (e.target);
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;

  if (isSiblingMode()) {
    const isDupe = typeof win._getDupeGroups === "function" && win._getDupeGroups() !== null;
    switch (e.key) {
      case "Escape":
        e.preventDefault();
        // Stop sibling bubble handlers (slideshow, calendar) from also
        // processing this ESC. Dialog (capture phase) runs first.
        e.stopImmediatePropagation();
        if (isDupe) win._dupeSkipOrClose?.();
        else closeCompare();
        break;
      case "ArrowLeft":
        e.preventDefault();
        _siblingNav(-1);
        break;
      case "ArrowRight":
        e.preventDefault();
        if (isDupe && getSiblings().length <= 1) win._dupeSkip?.();
        else _siblingNav(1);
        break;
      case "d":
      case "D":
      case "Delete":
      case "Backspace":
        e.preventDefault();
        _siblingDelete();
        break;
      case "Enter":
        e.preventDefault();
        _siblingUseThis();
        break;
    }
    return;
  }
  switch (e.key) {
    case "Escape":
      e.preventDefault();
      // Sibling bubble handlers don't get to also process this ESC.
      e.stopImmediatePropagation();
      closeCompare();
      break;
    case "1":
      e.preventDefault();
      comparePick("left");
      break;
    case "2":
      e.preventDefault();
      comparePick("right");
      break;
    case "s":
    case "S":
      if (!e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        compareSwap();
      }
      break;
    case "a":
    case "A":
      e.preventDefault();
      compareNav("left", -1);
      break;
    case "d":
    case "D":
      e.preventDefault();
      compareNav("left", 1);
      break;
    case "j":
    case "J":
      e.preventDefault();
      compareNav("right", -1);
      break;
    case "l":
    case "L":
      e.preventDefault();
      compareNav("right", 1);
      break;
    case "ArrowLeft":
      e.preventDefault();
      compareNav("left", -1);
      break;
    case "ArrowRight":
      e.preventDefault();
      compareNav("right", 1);
      break;
    case "c":
    case "C":
      e.preventDefault();
      closeCompare();
      break;
  }
});

import {
  _renderSiblingLabels,
  _renderSiblingToolbar,
  _siblingJump,
  openCompareWithSibling,
} from "./compare-sibling.mjs";
export {
  _renderSiblingLabels,
  _renderSiblingToolbar,
  _siblingDelete,
  _siblingJump,
  _siblingNav,
  _siblingUseThis,
  openCompareWithSibling,
};
