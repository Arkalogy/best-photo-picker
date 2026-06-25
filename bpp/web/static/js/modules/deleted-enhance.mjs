// @ts-check
/**
 * Photo enhance / revert-enhance bulk operations.
 *
 * Extracted from deleted.mjs during the v0.1 cleanup. The two main
 * operations hit POST /api/v1/photos/enhance and /reset-edits, then
 * surgically update the relevant grid cards (badge, thumb bust, pulse)
 * instead of a full vgrid re-render — the per-card mutation makes the
 * action feel immediate even with thousands of cards mounted.
 *
 * Re-exported from deleted.mjs.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * @param {string[]} filepaths
 */
export async function enhancePhotos(filepaths) {
  /** @type {any} */
  const win = window;
  if (!filepaths || filepaths.length === 0) return;
  try {
    const data = await apiFetch("/api/v1/photos/enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    if (data.error) {
      toast(data.error, true);
      return;
    }
    const photos = /** @type {any[]} */ (win.photos || []);
    for (const fp of filepaths) {
      const p = photos.find((x) => x.filepath === fp);
      if (p) p._enhanced = true;
    }
    if (data.enhanced === 0) {
      toast("Nothing to enhance", true);
      return;
    }
    toast(`Enhanced ${data.enhanced} photo${data.enhanced !== 1 ? "s" : ""}`);
    const ICONS = win.ICONS || {};
    const grid = document.getElementById("photo-grid");
    for (const fp of filepaths) {
      const p = photos.find((x) => x.filepath === fp);
      if (!p || !grid) continue;
      const card = /** @type {HTMLElement | null} */ (
        [...grid.querySelectorAll(".card")].find((c) => /** @type {HTMLElement} */ (c).dataset.filepath === fp) || null
      );
      if (!card) continue;
      const cardImg = card.querySelector(".card-image");
      if (cardImg && !cardImg.querySelector(".edited-badge")) {
        const badge = document.createElement("div");
        badge.className = "edited-badge";
        badge.title = "Edited";
        badge.innerHTML = ICONS.pencil || "✎";
        cardImg.appendChild(badge);
      }
      const img = /** @type {HTMLImageElement | null} */ (card.querySelector("img"));
      if (img && p.thumb_hash) img.src = authedSrc(`/thumb/${p.thumb_hash}?t=${Date.now()}`);
      if (win.vgrid?._cardCache) {
        const idx = parseInt(card.dataset.idx || "-1", 10);
        if (idx >= 0) win.vgrid._cardCache.delete(idx);
      }
      card.classList.add("card-enhanced-pulse");
      setTimeout(() => { if (card.parentElement) card.classList.remove("card-enhanced-pulse"); }, 1000);
    }
    win.clearMultiSelect?.();
  } catch (e) {
    toastError("enhance the photos", e);
  }
}

/**
 * @param {string[]} filepaths
 */
export async function revertEnhance(filepaths) {
  /** @type {any} */
  const win = window;
  if (!filepaths || filepaths.length === 0) return;
  try {
    const data = await apiFetch("/api/v1/photos/reset-edits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    if (data.error) {
      toast(data.error, true);
      return;
    }
    const photos = /** @type {any[]} */ (win.photos || []);
    for (const fp of filepaths) {
      const p = photos.find((x) => x.filepath === fp);
      if (p) { p._enhanced = false; p._auto_enhanced = false; }
    }
    toast(`Reverted ${data.reset} photo${data.reset !== 1 ? "s" : ""}`);
    const grid = document.getElementById("photo-grid");
    /** @type {Record<string, HTMLElement>} */
    const cardMap = {};
    if (grid) {
      for (const c of grid.querySelectorAll(".card")) {
        const fp = /** @type {HTMLElement} */ (c).dataset.filepath;
        if (fp) cardMap[fp] = /** @type {HTMLElement} */ (c);
      }
    }
    for (const fp of filepaths) {
      const p = photos.find((x) => x.filepath === fp);
      if (!p || !grid) continue;
      const card = cardMap[fp] || null;
      if (!card) continue;
      card.querySelector(".edited-badge")?.remove();
      const img = /** @type {HTMLImageElement | null} */ (card.querySelector("img"));
      if (img && p.thumb_hash) img.src = authedSrc(`/thumb/${p.thumb_hash}?t=${Date.now()}`);
      if (win.vgrid?._cardCache) {
        const idx = parseInt(card.dataset.idx || "-1", 10);
        if (idx >= 0) win.vgrid._cardCache.delete(idx);
      }
      const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
      const curAlbum = (/** @type {any[]} */ (win.albumList || [])).find(
        (a) => a.id === win.currentAlbumId
      );
      const inEnhancedView =
        filterEl?.value === "enhanced" || curAlbum?.album_type === "smart_enhanced";
      card.classList.add("card-reverted-pulse");
      setTimeout(() => {
        if (!card.parentElement) return;
        card.classList.remove("card-reverted-pulse");
        if (inEnhancedView) card.style.display = "none";
      }, 900);
    }
    const filterEl2 = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
    const curAlbum2 = (/** @type {any[]} */ (win.albumList || [])).find(
      (a) => a.id === win.currentAlbumId
    );
    if (filterEl2?.value === "enhanced" || curAlbum2?.album_type === "smart_enhanced") {
      setTimeout(() => win.renderGrid?.(), 1000);
    }
    const filterEl3 = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
    const curAlbum3 = (/** @type {any[]} */ (win.albumList || [])).find(
      (a) => a.id === win.currentAlbumId
    );
    if (filterEl3?.value === "enhanced" || curAlbum3?.album_type === "smart_enhanced") {
      win.loadAlbumList?.();
    }
    win.clearMultiSelect?.();
  } catch (e) {
    toastError("revert the enhancement", e);
  }
}

export async function batchEnhance() {
  /** @type {any} */
  const win = window;
  const ms = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const fps = [...ms];
  if (fps.length === 0) return;
  await enhancePhotos(fps);
}
