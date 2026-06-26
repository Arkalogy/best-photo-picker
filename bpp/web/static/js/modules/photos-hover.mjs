// @ts-check
/**
 * Grid hover handlers: video-sprite preview + dupe-cluster highlight.
 *
 * Extracted from photos.mjs during the v0.1 cleanup. Two IIFEs that
 * wire grid-level event listeners at module load:
 *
 *   * Video hover sprite scrub — hovering a video card streams
 *     /sprite/<hash>.png and animates a canvas overlay.
 *   * Dupe cluster highlight — hovering a card in a dupe group
 *     adds .dupe-highlight to its siblings.
 *
 * No exports — pure side-effect wiring on import. Listed in the
 * index.html modules-bridge to ensure the import fires at load.
 */

import { authedSrc } from "./api-client.mjs";
import { vgrid } from "./photos.mjs";


// ── Video hover preview (sprite scrub) ──
(() => {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  /** @type {Record<string, HTMLImageElement>} */
  const _spriteCache = {};
  /** @type {{card: HTMLElement, hash: string, sprite: HTMLImageElement | null, canvas: HTMLCanvasElement | null} | null} */
  let _activePreview = null;

  grid.addEventListener(
    "mouseenter",
    (e) => {
      const target = /** @type {HTMLElement} */ (e.target);
      const card = /** @type {HTMLElement | null} */ (target.closest && target.closest(".card"));
      if (!card) return;
      const idx = parseInt(card.dataset.idx || "", 10);
      const p = vgrid.items[idx];
      if (!p || !p.is_video || !p.thumb_hash) return;
      _startPreview(card, p);
    },
    true
  );

  grid.addEventListener(
    "mouseleave",
    (e) => {
      const target = /** @type {HTMLElement} */ (e.target);
      const card = target.closest && target.closest(".card");
      if (card) _stopPreview();
    },
    true
  );

  grid.addEventListener("mousemove", (e) => {
    if (!_activePreview) return;
    const target = /** @type {HTMLElement} */ (e.target);
    const card = /** @type {HTMLElement | null} */ (target.closest && target.closest(".card"));
    if (!card || card !== _activePreview.card) return;
    const imgEl = /** @type {HTMLImageElement | null} */ (card.querySelector(".card-image img"));
    if (!imgEl || !_activePreview.sprite) return;
    const rect = imgEl.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const frameIdx = Math.min(7, Math.floor(x * 8));
    const frameW = _activePreview.sprite.naturalWidth / 8;
    const frameH = _activePreview.sprite.naturalHeight;
    if (!_activePreview.canvas) {
      const c = document.createElement("canvas");
      c.style.cssText =
        "position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;pointer-events:none;z-index:1;border-radius:inherit;";
      card.querySelector(".card-image")?.appendChild(c);
      _activePreview.canvas = c;
    }
    const c = _activePreview.canvas;
    const dw = imgEl.clientWidth;
    const dh = imgEl.clientHeight;
    if (c.width !== dw || c.height !== dh) {
      c.width = dw;
      c.height = dh;
    }
    const ctx2d = c.getContext("2d");
    if (!ctx2d) return;
    const sx = frameIdx * frameW;
    const srcAspect = frameW / frameH;
    const dstAspect = dw / dh;
    let sw, sh, sy;
    if (srcAspect > dstAspect) {
      sh = frameH;
      sw = frameH * dstAspect;
      sy = 0;
    } else {
      sw = frameW;
      sh = frameW / dstAspect;
      sy = (frameH - sh) / 2;
    }
    ctx2d.drawImage(_activePreview.sprite, sx + (frameW - sw) / 2, sy, sw, sh, 0, 0, dw, dh);
  });

  /**
   * @param {HTMLElement} card
   * @param {any} p
   */
  function _startPreview(card, p) {
    _stopPreview();
    const hash = p.thumb_hash;
    _activePreview = { card, hash, sprite: null, canvas: null };
    if (_spriteCache[hash]) {
      _activePreview.sprite = _spriteCache[hash];
      return;
    }
    const img = new Image();
    img.onload = () => {
      _spriteCache[hash] = img;
      if (_activePreview && _activePreview.hash === hash) _activePreview.sprite = img;
    };
    img.src = authedSrc("/api/v1/video/preview/" + hash);
  }

  function _stopPreview() {
    if (_activePreview && _activePreview.canvas) {
      _activePreview.canvas.remove();
    }
    _activePreview = null;
  }
})();

// The legacy dupe-group hover-highlight (dimmed the whole grid + outlined a
// hovered photo's near-dupe siblings) was removed: Moments now show grouping
// persistently via the per-card frame, so the transient full-grid dim was
// redundant and read as noise. The Duplicates album still covers that workflow.
