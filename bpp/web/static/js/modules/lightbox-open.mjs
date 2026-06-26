// @ts-check
/**
 * openLightbox + refreshLightboxIfOpen — the entry point that swaps a
 * new photo into the full-screen viewer and refreshes every panel
 * (action bar, EXIF, map, video, trim, tags, faces, pets, similar).
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. Re-exported
 * from lightbox.mjs.
 */

import { authedSrc } from "./api-client.mjs";
import { formatDate } from "./date-format.mjs";
import { qualityLabel } from "./score-format.mjs";
import { saveNavState } from "./navigation.mjs";
import { sensitiveChipHTML } from "./sensitive.mjs";
import { updateLightboxSimilar } from "./clip.mjs";
import { updateLightboxTags } from "./tags.mjs";
import {
  updateLightboxExif,
  updateLightboxMap,
  updateLightboxTrim,
  updateLightboxVideoInfo,
} from "./lightbox-info.mjs";
import { _lbEndFaceEdit } from "./lightbox-face-edit.mjs";
import { updateLightboxPets } from "./lightbox-face-assign.mjs";
import { lbResetZoom } from "./lightbox-input.mjs";
import {
  lbSwitchTab,
  updateLightboxActions,
  updateLightboxFaces,
} from "./lightbox.mjs";

/**
 * @param {string} raw
 */
function _formatLbDate(raw) {
  return formatDate(raw, "time");
}

/**
 * Render the Quality section (headline verdict + score bars + the
 * sensitive chip) into #lb-scores. Exported so the sensitive-flag
 * toggle can re-render just this panel without reloading the photo.
 * @param {any} p photo dict
 */
export function updateLightboxScores(p) {
  /** @type {any} */
  const win = window;
  const q = qualityLabel(p.aggregate_score);
  const pct = ((p.aggregate_score || 0) * 100).toFixed(0);
  const scoreTitle = `BPP Quality Score: ${pct}%\nAverage of Sharpness, Exposure, Faces, and Composition.\nGreat (70%+) · Good (50-70%) · Fair (30-50%) · Low (<30%)`;

  const SCORE_LABELS = win.SCORE_LABELS || {};
  const scores = [
    { key: "blur_score", v: p.blur_score || 0 },
    { key: "exposure_score", v: p.exposure_score || 0 },
    { key: "face_score", v: p.face_score || 0 },
    { key: "composition_score", v: p.composition_score || 0 },
  ];
  const _scoresEl = document.getElementById("lb-scores");
  if (_scoresEl)
    _scoresEl.innerHTML =
      // Branded verdict (user direction): "BPP SCORE" as the section label
      // (same style as PEOPLE/INFO), then tier-word-first — "Great (82%)".
      // Fully monochrome: no tier color anywhere in the panel.
      `<div class="lb-section-label">BPP Score</div>` +
      `<div class="lb-quality-headline" title="${scoreTitle}">` +
      `<span class="lb-quality-tier">${q.text}</span>` +
      `<span class="lb-quality-pct">(${pct}%)</span></div>` +
      scores
        .map(
          // Monochrome bars (user call): the % already says what the color
          // said, and four hues in one block read as noise. The headline
          // verdict above carries the section's only tier color.
          (s) => `<div class="lb-score-row">
      <span class="lb-score-label">${SCORE_LABELS[s.key]}</span>
      <div class="lb-score-bar"><div class="lb-score-fill" style="width:${(s.v * 100).toFixed(0)}%"></div></div>
      <span class="lb-score-val">${(s.v * 100).toFixed(0)}%</span>
    </div>`,
        )
        .join("") +
      sensitiveChipHTML(p);
}

/**
 * @param {number} idx
 * @param {boolean | string} [animClass]
 */
export function openLightbox(idx, animClass) {
  // Protection F: wrap the full lightbox open in a top-level try/catch.
  // A render exception here used to leave the user stuck — lightbox
  // overlay visible but with broken state — and only a page reload
  // could recover. Now any throw falls back to closing the lightbox
  // and toasting the failure so the user can try another photo.
  try {
    _doOpenLightbox(idx, animClass);
  } catch (err) {
    console.warn("[lightbox] openLightbox threw — closing and toasting", err);
    try {
      /** @type {any} */ (window).closeLightbox?.();
    } catch (_closeErr) {
      // Force-close via class swap if closeLightbox itself errored.
      const lb = document.getElementById("lightbox");
      if (lb) lb.classList.remove("visible");
    }
    import("./toast.mjs").then((m) => {
      m.toast?.("Couldn't open this photo. Try another or reload.", "error");
    }).catch(() => {});
  }
}

/**
 * @param {number} idx
 * @param {boolean | string} [animClass]
 */
function _doOpenLightbox(idx, animClass) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (idx < 0 || idx >= items.length) return;
  // End any in-progress face edit (drag-to-fix, Add-face placeholder)
  // before swapping photos. Otherwise _lbEdit still holds the old
  // photo's thumb_hash + four document-level listeners; a stray
  // mouseup would POST /faces/update-bbox or /faces/create against
  // the *previous* photo while the user is looking at a new one.
  _lbEndFaceEdit();
  win.hideCardCtxMenu?.();
  lbResetZoom();
  win.lightboxIdx = idx;
  const p = items[idx];
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  const video = /** @type {HTMLVideoElement | null} */ (document.getElementById("lb-video"));
  const flash = document.getElementById("lb-flash");
  if (!img || !video || !flash) return;

  img.className = "lightbox-img";
  flash.className = "lb-flash";
  const actionText = document.getElementById("lb-action-text");
  if (actionText) actionText.className = "lb-action-text";

  if (p.is_video) {
    img.classList.add("hidden");
    video.classList.remove("hidden");
    video.src = authedSrc("/video/" + p.thumb_hash);
    video.poster = authedSrc("/thumb/" + p.thumb_hash);
  } else {
    img.classList.remove("hidden");
    video.classList.add("hidden");
    video.pause();
    video.removeAttribute("src");
    img.removeAttribute("src");
    const photoUrl = authedSrc("/photo/" + p.thumb_hash + (p._enhanced ? "?t=" + Date.now() : ""));
    img.onerror = () => {
      console.error("[lightbox] Full photo failed to load:", photoUrl, "— falling back to thumbnail");
      img.onerror = null;
      img.src = authedSrc("/thumb/" + p.thumb_hash + "?t=" + Date.now());
    };
    img.src = photoUrl;
  }
  const fnEl = document.getElementById("lb-filename");
  if (fnEl) {
    fnEl.textContent = p.filename;
    // Hover shows the full path; click copies it (data-action in markup).
    fnEl.title = `${p.filepath}\nClick to copy file path`;
  }
  const dateEl = /** @type {HTMLElement | null} */ (document.getElementById("lb-date"));
  const dateInput = document.getElementById("lb-date-input");
  if (dateEl) {
    dateEl.textContent = _formatLbDate(p.date || p.date_day || "");
    dateEl.classList.remove("hidden");
    dateEl.style.cursor = "default";
    dateEl.title = "";
    dateEl.onclick = null;
  }
  if (dateInput) dateInput.classList.add("hidden");

  // Panel-cleanup item 2: the aggregate verdict LEADS the Quality section
  // (big, tier-colored) instead of floating as small text in the header —
  // verdict and the bars that explain it live together. The header's
  // #lb-quality slot stays empty (date + filename only up there).
  const qEl = /** @type {HTMLElement | null} */ (document.getElementById("lb-quality"));
  if (qEl) qEl.textContent = "";
  updateLightboxScores(p);

  updateLightboxActions(p);
  updateLightboxExif(p);
  updateLightboxMap(p);
  updateLightboxVideoInfo(p);
  updateLightboxTrim(p);
  updateLightboxTags(p);
  updateLightboxFaces(p);
  updateLightboxPets(p);

  // Moment membership wins over the CLIP near-dup cluster as the "similar"
  // surface: a Moment is a persisted, user-meaningful burst (moment_cluster_id
  // + moment_size), so when this photo belongs to one we populate the strip
  // from its fellow Moment shots and flag it so the panel + compare overlay
  // read as a Moment. The currently-open photo is the parent (left side of
  // compare); its fellow members are the siblings.
  if ((!p.similar_photos || p.similar_photos.length === 0) && p.moment_cluster_id && (p.moment_size || 1) > 1) {
    const pool = /** @type {any[]} */ (
      (win.photos && win.photos.length ? win.photos : win.currentGridItems) || []
    );
    const members = pool.filter(
      (s) => s && s.moment_cluster_id === p.moment_cluster_id && s.filepath !== p.filepath && !s.deleted_at,
    );
    if (members.length > 0) {
      p._isMoment = true;
      p.similar_photos = members.map((s) => ({
        filepath: s.filepath,
        thumb_hash: s.thumb_hash,
        similarity: null,
        aggregate_score: s.aggregate_score || 0,
        blur_score: s.blur_score || 0,
        exposure_score: s.exposure_score || 0,
        face_score: s.face_score || 0,
        composition_score: s.composition_score || 0,
        date_day: s.date_day || "",
        filename: s.filename || s.original_filename || "",
      }));
    }
  }

  if ((!p.similar_photos || p.similar_photos.length === 0) && win._simClusterMap) {
    const clusterPaths = win._simClusterMap[p.filepath];
    if (clusterPaths && clusterPaths.length > 1) {
      const itemsByPath = /** @type {Record<string, any>} */ ({});
      for (const item of /** @type {any[]} */ (win.currentGridItems || [])) {
        itemsByPath[item.filepath] = item;
      }
      p.similar_photos = clusterPaths
        .filter((fp) => fp !== p.filepath)
        .map((fp) => {
          const s = itemsByPath[fp];
          return s
            ? {
                filepath: s.filepath,
                thumb_hash: s.thumb_hash,
                similarity: null,
                aggregate_score: s.aggregate_score || 0,
                blur_score: s.blur_score || 0,
                exposure_score: s.exposure_score || 0,
                face_score: s.face_score || 0,
                composition_score: s.composition_score || 0,
                date_day: s.date_day || "",
                filename: s.filename || s.original_filename || "",
              }
            : null;
        })
        .filter(Boolean);
    }
  }

  updateLightboxSimilar(p);
  lbSwitchTab();
  document.getElementById("lightbox")?.classList.add("visible");
  saveNavState();

  if (animClass) {
    requestAnimationFrame(() => {
      img.classList.add("anim-enter");
      img.addEventListener(
        "animationend",
        () => img.classList.remove("anim-enter"),
        { once: true },
      );
    });
  }
}

export function refreshLightboxIfOpen() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
    const p = items[win.lightboxIdx];
    updateLightboxFaces(p);
    updateLightboxActions(p);
  }
}
