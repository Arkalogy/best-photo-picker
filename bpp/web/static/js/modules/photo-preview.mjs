// @ts-check
/**
 * Full-photo preview scrim, reusable by any surface that shows small
 * crops/thumbnails the user can't judge from (face-review avatars, the
 * Ignored-faces grid). Opens the full image (/photo/<hash>) over whatever
 * modal is showing, with a visible close button + caption (filename ·
 * timestamp · score) so it's obvious how to dismiss and what you're
 * looking at. Click anywhere, the × button, or Esc closes it WITHOUT
 * closing the modal underneath (capture-phase Esc + stopImmediatePropagation).
 *
 * The keydown handler is module-level (stable reference, idempotent
 * add/remove) so it can't leak/accumulate when the overlay is torn down by
 * something other than close() — e.g. a modal re-render.
 */

import { authedSrc } from "./api-client.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { formatDate } from "./date-format.mjs";

function _closePhotoPreview() {
  document.getElementById("photo-preview-overlay")?.remove();
  document.removeEventListener("keydown", _photoPreviewKey, true);
}

/** @param {KeyboardEvent} e */
function _photoPreviewKey(e) {
  if (e.key === "Escape") {
    e.stopImmediatePropagation(); // close the preview, NOT the modal beneath
    _closePhotoPreview();
  }
}

/**
 * Build the caption line shown under the preview image.
 * @param {string} [filename]
 * @param {string} [date]
 * @param {number|string} [score] 0..1 aggregate score
 * @returns {string} HTML ("" when there's nothing to show)
 */
function _previewCaption(filename, date, score) {
  /** @type {string[]} */
  const parts = [];
  if (filename) parts.push(`<span class="pp-cap-name">${esc(filename)}</span>`);
  const ts = date ? formatDate(date, "time") : "";
  if (ts) parts.push(`<span>${esc(ts)}</span>`);
  const s = score === "" || score == null ? NaN : Number(score);
  if (isFinite(s)) parts.push(`<span class="pp-cap-score">Score ${Math.round(s * 100)}</span>`);
  if (!parts.length) return "";
  return `<div class="photo-preview-caption">${parts.join(
    '<span class="pp-cap-sep">·</span>',
  )}</div>`;
}

/**
 * Show the full photo for `hash` (a thumb/path hash). No-op on empty hash.
 * Optional metadata renders as a caption so the user knows which photo and
 * how good it scored.
 * @param {string} hash
 * @param {string} [filename]
 * @param {string} [date]
 * @param {number|string} [score]
 */
export function openPhotoPreview(hash, filename, date, score) {
  if (!hash) return;
  let ov = document.getElementById("photo-preview-overlay");
  if (!ov) {
    ov = document.createElement("div");
    ov.id = "photo-preview-overlay";
    ov.className = "photo-preview-overlay";
    ov.addEventListener("click", _closePhotoPreview);
    document.removeEventListener("keydown", _photoPreviewKey, true); // idempotent
    document.addEventListener("keydown", _photoPreviewKey, true);
    document.body.appendChild(ov);
  }
  ov.innerHTML =
    `<button type="button" class="photo-preview-close" aria-label="Close preview" title="Close (Esc)">&times;</button>` +
    `<img src="${escapeAttr(authedSrc("/photo/" + hash))}" alt="full photo">` +
    _previewCaption(filename, date, score);
}
