// @ts-check
/**
 * renderCardHTML — builds the HTML string for one photo grid card.
 *
 * Extracted from photos.mjs during the v0.1 cleanup. Reads selection /
 * override / favorite state off `window`, then assembles the card with
 * action buttons, badges (dedup / deleted / override / pet / video /
 * raw / edited / live), thumbnail, score badge, and the per-card
 * score overlay. Pure HTML composition, no DOM mutation.
 *
 * Re-exported from photos.mjs.
 */

import { authedSrc } from "./api-client.mjs";
import { escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { formatDateStamp } from "./date-format.mjs";
import { scoreBadgeBg } from "./score-format.mjs";
import { _formatDuration } from "./photos.mjs";
import { momentClasses } from "./moments-view.mjs";

/**
 * @param {any} p
 * @param {number} idx
 * @returns {string}
 */
export function renderCardHTML(p, idx) {
  /** @type {any} */
  const win = window;
  const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const albums = /** @type {any[]} */ (win.albumList || []);
  const ICONS = win.ICONS || {};

  const isSel = selectedPaths.has(p.filepath);
  const ov = overrides[p.filepath];
  const isFav = favorites.has(p.filepath);
  const isDeleted = !!p.deleted_at;
  let cls = "card";
  if (isDeleted) cls += " is-deleted";
  else if (ov === "include") cls += " force-included selected";
  else if (ov === "exclude") cls += " force-excluded";
  else if (isSel) cls += " selected";
  if (isFav) cls += " is-fav";
  const msCls = multiSelected.has(p.filepath) ? " multi-selected" : "";

  const incCls = ov === "include" ? " active-include" : "";
  const favCls = isFav ? " active-fav" : "";
  const album = win.currentAlbumId ? albums.find((a) => a.id === win.currentAlbumId) : null;
  const delLabel = album && album.album_type === "manual" ? "Remove" : "Delete";

  const score = p.aggregate_score || 0;
  const scoreBg = scoreBadgeBg(score);
  const bars = [
    { l: "Sharp", v: p.blur_score || 0 },
    { l: "Expo", v: p.exposure_score || 0 },
    { l: "Face", v: p.face_score || 0 },
    { l: "Comp", v: p.composition_score || 0 },
  ]
    .map(
      (s) => `<div class="score-row">
        <span class="score-label">${s.l}</span>
        <div class="score-bar"><div class="score-fill" style="width:${(s.v * 100).toFixed(0)}%"></div></div>
        <span class="score-val">${(s.v * 100).toFixed(0)}%</span>
      </div>`
    )
    .join("");

  const dedupBadge =
    (p.cluster_size || 1) > 1 ? `<div class="dedup-badge">&times;${p.cluster_size}</div>` : "";
  const deletedBadge = isDeleted ? `<div class="deleted-badge">Deleted</div>` : "";
  const overrideBadge =
    ov === "include"
      ? `<div class="override-badge">Pick</div>`
      : ov === "exclude"
        ? `<div class="override-badge">Skip</div>`
        : "";
  const petBadge =
    p.has_cat || p.has_dog
      ? `<div class="pet-badge" title="${p.has_cat && p.has_dog ? "Cat & Dog" : p.has_cat ? "Cat" : "Dog"}">${ICONS.paw || ""}</div>`
      : "";
  const videoBadge = p.is_video
    ? `<div class="video-badge" title="Video">&#9654;${p.video_duration ? " " + _formatDuration(p.video_duration) : ""}</div>`
    : "";
  const rawBadge = p.is_raw ? `<div class="raw-badge" title="RAW">RAW</div>` : "";
  const editedBadge = p._enhanced
    ? `<div class="edited-badge" title="Edited">${ICONS.pencil || ""}</div>`
    : "";
  const liveBadge =
    p.live_photo_sidecar_count > 0
      ? `<div class="live-badge" title="Live Photo">&#x29BF;</div>`
      : "";

  // Moment grouping: cards of one Moment share a calm accent frame (two
  // alternating shades by moment parity) so the similar shots read as one
  // group in place; the keeper stays bright while prune-candidates dim
  // (CSS). Frame-only, no badge — never competes with the other card badges.
  const momentCls = momentClasses(p, win.momentKeepers);
  // In the Moments album each card is a burst "stack" (a cover with
  // _momentCount siblings); badge the count + add a stacked-edge class so
  // it reads as a group, not a single photo. Click → compare overlay.
  const stackCount = p._momentCount && p._momentCount > 1 ? p._momentCount : 0;
  const momentStyle = "";
  const momentBadge = stackCount
    ? `<div class="moment-stack-badge" title="${stackCount} similar shots — click to compare & prune">` +
      `${ICONS.layers || "&#9783;"} ${stackCount}</div>`
    : "";

  return `<div class="${cls}${msCls}${momentCls}${stackCount ? " moment-stack" : ""}"${momentStyle} data-action="handleCardClick" data-pass-event="true" data-arg0="${idx}" data-oncontextmenu="_bppCardCtxMenu" data-filepath="${escapeJsAttr(p.filepath)}" data-idx="${idx}">
      <div class="card-actions">
        <button class="card-action${favCls}" data-stop-propagation="true" data-action="toggleFavorite" data-arg0="${escapeJsAttr(p.filepath)}" title="${isFav ? "Remove from favorites" : "Add to favorites"}" aria-label="Favorite">&#9829;</button>
        <button class="card-action${incCls}" data-stop-propagation="true" data-action="setOverride" data-arg0="${escapeJsAttr(p.filepath)}" data-arg1="include" title="${ov === "include" ? "Clear override" : "Always include this photo"}" aria-label="Force include">&#10003;</button>
        <button class="card-action card-action-delete" data-stop-propagation="true" data-action="deleteFromCard" data-arg0="${escapeJsAttr(p.filepath)}" title="Move to trash" aria-label="${escapeAttr(delLabel)}">${ICONS.trash || ""}</button>
      </div>
      <div class="card-image thumb-loading">
        <img src="${authedSrc("/thumb/" + p.thumb_hash)}" loading="lazy" decoding="async" alt="${escapeAttr(p.filename)}" data-onload="_bppThumbLoaded">
        ${dedupBadge}${deletedBadge}${overrideBadge}${petBadge}${videoBadge}${rawBadge}${editedBadge}${liveBadge}${momentBadge}
        <div class="score-badge" style="background:${scoreBg}">${(score * 100).toFixed(0)}%</div>
        <div class="score-overlay">${bars}</div>
        ${p.date ? `<div class="card-date-stamp">${formatDateStamp(p.date)}</div>` : ""}
      </div>
    </div>`;
}
