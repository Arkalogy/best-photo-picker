// @ts-check
/**
 * One-line photo metadata for the face-review popups (People review,
 * "Same person?" pairs). A tight face crop is hard to judge — showing the
 * source photo's filename, timestamp, and score gives the user the same
 * context the compare overlay already provides for dedup/Moments pruning.
 */

import { esc, escapeAttr } from "./text-format.mjs";
import { formatDate } from "./date-format.mjs";

/**
 * @typedef {Object} ReviewMeta
 * @property {string} [filename]
 * @property {string} [date]
 * @property {number} [score]  0..1 aggregate score
 */

/**
 * Render `filename · timestamp · score` for a review crop. Returns an empty
 * string when there's nothing to show (so callers can interpolate blindly).
 * @param {ReviewMeta | null | undefined} m
 * @returns {string} HTML
 */
export function reviewMetaLine(m) {
  if (!m) return "";
  // Three stacked, centered lines (filename / timestamp / score). Stacking
  // — rather than inline parts joined by separators — avoids an orphaned
  // "· Score" when a long timestamp wraps under a narrow avatar column.
  /** @type {string[]} */
  const rows = [];
  if (m.filename) {
    rows.push(
      `<span class="review-meta-name" title="${escapeAttr(m.filename)}">${esc(m.filename)}</span>`,
    );
  }
  const ts = m.date ? formatDate(m.date, "time") : "";
  if (ts) rows.push(`<span class="review-meta-date">${esc(ts)}</span>`);
  if (typeof m.score === "number") {
    rows.push(`<span class="review-meta-score">Score ${Math.round(m.score * 100)}</span>`);
  }
  if (!rows.length) return "";
  return `<div class="review-meta-line">${rows.join("")}</div>`;
}

/**
 * Plain-text version for `title=` tooltips on sample thumbnails.
 * @param {ReviewMeta | null | undefined} m
 * @returns {string}
 */
export function reviewMetaText(m) {
  if (!m) return "";
  const parts = [];
  if (m.filename) parts.push(m.filename);
  if (m.date) parts.push(formatDate(m.date, "time"));
  if (typeof m.score === "number") parts.push(`Score ${Math.round(m.score * 100)}`);
  return parts.join(" · ");
}
