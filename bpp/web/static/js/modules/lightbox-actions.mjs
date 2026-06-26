// @ts-check
/**
 * Lightbox action bar, navigation, undo stack, close/panel/highlight.
 * (The right-click context menu lives in lightbox-ctxmenu.mjs.)
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. This module
 * owns the "what does the user click" surface of the lightbox:
 *
 *   * Action bar render          — updateLightboxActions
 *   * Enhance / revert-enhance   — lbEnhance, lbRevertEnhance
 *   * Favorite toggle            — lbToggleFav
 *   * Show in Finder / Reveal    — lbShowInFinder
 *   * Delete (soft)              — lbDelete
 *   * Include / Exclude          — lbAction (animated swipe-out)
 *   * Navigation                 — lightboxNav (prev / next photo)
 *   * Undo last include/exclude  — lbUndo (drains lbUndoStack)
 *   * Close                      — closeLightbox
 *   * Bottom panel toggle        — _lbTogglePanel
 *   * Selected-card highlight    — _highlightGalleryCard
 *
 * State that travels with this module:
 *   `lbAnimating`     — true while a swipe-out animation is mid-frame
 *   `lbUndoStack`     — last N include/exclude operations
 *   `refreshLightboxIfOpen` — moved here because the action bar's
 *                             re-render loop wants it nearby
 *
 * Cross-module dependencies still live in lightbox.mjs:
 *   lbResetZoom, openLightbox, updateLightboxFaces.
 *   Imported back here via the existing circular-import pattern.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { toast, toastError } from "./toast.mjs";
import { deletePhotos } from "./deleted.mjs";
import { loadAlbumList } from "./albums.mjs";
import { openEditor } from "./editor.mjs";
import { saveNavState } from "./navigation.mjs";
import { saveOverrides, toggleFavorite } from "./toolbar.mjs";
import { _lbEndFaceEdit } from "./lightbox-face-edit.mjs";
import { scheduleRecompute } from "./analysis.mjs";
import { setOverride, updateCardInPlace, vgrid } from "./photos.mjs";
import {
  _lbActiveCleanups,
  _lbApplyTransform,
  lbResetZoom,
  openLightbox,
  updateLightboxFaces,
} from "./lightbox.mjs";

let lbAnimating = false;
/** @type {Array<{filepath: string, prevOverride: string | null, idx: number}>} */
let lbUndoStack = [];


export function updateLightboxActions(p) {
  /** @type {any} */
  const win = window;
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const ov = overrides[p.filepath] || "";
  const upAct = ov === "include" ? " active" : "";
  const downAct = ov === "exclude" ? " active" : "";
  const undoVis = lbUndoStack.length > 0 ? " visible" : "";
  const isFav = favorites.has(p.filepath);

  // Unified header: include/exclude/favorite/delete/undo live in the
  // editor bar above the image (static markup in index.html) alongside
  // the editing tools — this function only SYNCS their state per photo.
  // The panel's #lb-actions slot stays empty (hidden via :empty CSS);
  // it's cleared here so the editor's legacy panel copy can't show a
  // stale rendered bar.
  const lbActionsEl = document.getElementById("lb-actions");
  if (lbActionsEl) lbActionsEl.innerHTML = "";

  /** @param {string} id @param {boolean} on */
  const setActive = (id, on) => {
    const b = document.getElementById(id);
    if (b) b.classList.toggle("active", on);
  };
  setActive("lb-top-include", upAct !== "");
  setActive("lb-top-exclude", downAct !== "");
  setActive("lb-top-fav", isFav);
  const undoBtn = document.getElementById("lb-undo");
  if (undoBtn) undoBtn.classList.toggle("visible", undoVis !== "");

  // Enhance tool flips between enhance and revert per photo state.
  const enhanceBtn = document.getElementById("editor-enhance-btn");
  if (enhanceBtn) {
    enhanceBtn.classList.toggle("active", !!p._auto_enhanced);
    enhanceBtn.setAttribute("data-action", p._auto_enhanced ? "lbRevertEnhance" : "lbEnhance");
    enhanceBtn.title = p._auto_enhanced
      ? "Revert auto-enhance"
      : "Auto-enhance brightness, contrast, color";
  }
}

export async function lbEnhance() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.querySelector('[aria-label="Enhance"]')
  );
  const origHtml = btn ? btn.innerHTML : null;
  if (btn) {
    const svg = btn.querySelector("svg");
    btn.innerHTML = (svg ? svg.outerHTML : "") + " Enhancing…";
  }
  try {
    const data = await apiFetch("/api/v1/photos/enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: [p.filepath] }),
    });
    if (data.error) {
      toast(data.error, true);
      return;
    }
    p._enhanced = true;
    p._auto_enhanced = true;
    if (vgrid) vgrid.render(true);
    const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
    if (img) img.src = authedSrc("/photo/" + p.thumb_hash + "?t=" + Date.now());
    updateLightboxActions(p);
    toast("Enhanced!");
    // Refresh sidebar only when the enhanced count is visible to the user.
    const _feFilter = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
    const _feAlbum = (/** @type {any[]} */ (win.albumList || [])).find((a) => a.id === win.currentAlbumId);
    if (_feFilter?.value === "enhanced" || _feAlbum?.album_type === "smart_enhanced") {
      win.loadAlbumList?.();
    }
  } catch (err) {
    console.error("Enhance failed:", err);
    toastError("enhance the photo", err);
    if (btn && origHtml) btn.innerHTML = origHtml;
  }
}

export async function lbRevertEnhance() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  try {
    await apiFetch("/api/v1/photos/reset-edits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: [p.filepath] }),
    });
    p._enhanced = false;
    p._auto_enhanced = false;
    const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
    if (img) img.src = authedSrc("/photo/" + p.thumb_hash + "?t=" + Date.now());
    updateLightboxActions(p);
    // Pulse the grid card to confirm revert visually
    const grid = document.getElementById("photo-grid");
    if (grid) {
      const card = /** @type {HTMLElement | null} */ (
        [...grid.querySelectorAll(".card")].find((c) => /** @type {HTMLElement} */ (c).dataset.filepath === p.filepath) || null
      );
      if (card) {
        card.querySelector(".edited-badge")?.remove();
        card.classList.add("card-reverted-pulse");
        setTimeout(() => card.classList.remove("card-reverted-pulse"), 1000);
      }
    }
    toast("Reverted to original");
    // Refresh sidebar only when the enhanced count is visible to the user.
    const _rvFilter = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
    const _rvAlbum = (/** @type {any[]} */ (win.albumList || [])).find((a) => a.id === win.currentAlbumId);
    if (_rvFilter?.value === "enhanced" || _rvAlbum?.album_type === "smart_enhanced") {
      win.loadAlbumList?.();
    }
  } catch (e) {
    toastError("revert the auto-enhance", e);
  }
}

export function lbToggleFav() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  toggleFavorite(p.filepath);
  updateLightboxActions(p);
}

/**
 * Copy the current photo's full file path to the clipboard — wired to a
 * click on the filename line. Works over LAN too (unlike Show in
 * Finder, which stays in the right-click menu and only makes sense on
 * the host machine).
 */
export async function lbCopyFilePath() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  if (!p || !p.filepath) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(p.filepath);
    } else {
      // Non-secure-context fallback (plain-http LAN): textarea trick.
      const ta = document.createElement("textarea");
      ta.value = p.filepath;
      ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast("File path copied");
  } catch (e) {
    toast("Couldn't copy file path: " + (/** @type {any} */ (e).message || "clipboard unavailable"), true);
  }
}

export async function lbShowInFinder() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  if (!p.filepath) return;
  try {
    await apiFetch("/api/v1/reveal-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepath: p.filepath }),
    });
  } catch (e) {
    toastError("reveal this file", e);
  }
}

export async function lbDelete() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  await deletePhotos([p.filepath]);
  closeLightbox();
}

/**
 * @param {string} action
 */
export function lbAction(action) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0 || lbAnimating) return;
  const p = items[win.lightboxIdx];
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const currentOv = overrides[p.filepath] || "";

  if (currentOv === action) {
    lbUndoStack.push({ filepath: p.filepath, prevOverride: currentOv, idx: win.lightboxIdx });
    setOverride(p.filepath, action);
    updateLightboxActions(p);
    return;
  }

  lbAnimating = true;
  lbResetZoom();
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  const flash = /** @type {HTMLElement | null} */ (document.getElementById("lb-flash"));
  if (!img || !flash) {
    lbAnimating = false;
    return;
  }

  lbUndoStack.push({
    filepath: p.filepath,
    prevOverride: currentOv || null,
    idx: win.lightboxIdx,
  });

  _lbClearAnimClasses(img);

  flash.className = "lb-flash flash-" + action;
  const actionText = document.getElementById("lb-action-text");
  if (actionText) {
    actionText.textContent = action === "include" ? "Included" : "Excluded";
    actionText.className = "lb-action-text show-" + action;
  }

  setOverride(p.filepath, action);

  const animOut = action === "include" ? "anim-include" : "anim-exclude";
  img.classList.add(animOut);

  setTimeout(() => {
    flash.className = "lb-flash";
    lbAnimating = false;
    const next = win.lightboxIdx + 1;
    if (next < items.length) {
      openLightbox(next, true);
    } else {
      closeLightbox();
    }
  }, 350);
}

/**
 * @param {HTMLElement} img
 */
export function _lbClearAnimClasses(img) {
  const wasHidden = img.classList.contains("hidden");
  img.className = "lightbox-img";
  if (wasHidden) img.classList.add("hidden");
  void img.offsetWidth;
}

/**
 * @param {number} dir
 */
export function lightboxNav(dir) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (lbAnimating) return;
  const next = win.lightboxIdx + dir;
  if (next < 0 || next >= items.length) return;

  lbResetZoom();
  lbAnimating = true;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) {
    lbAnimating = false;
    return;
  }
  _lbClearAnimClasses(img);
  img.classList.add(dir > 0 ? "anim-next" : "anim-prev");

  setTimeout(() => {
    lbAnimating = false;
    openLightbox(next, true);
  }, 250);
}

export function lbUndo() {
  /** @type {any} */
  const win = window;
  if (lbUndoStack.length === 0 || lbAnimating) return;
  lbAnimating = true;
  lbResetZoom();
  const entry = lbUndoStack.pop();
  if (!entry) {
    lbAnimating = false;
    return;
  }

  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  if (entry.prevOverride) {
    overrides[entry.filepath] = entry.prevOverride;
  } else {
    delete overrides[entry.filepath];
  }
  saveOverrides();
  scheduleRecompute();

  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) {
    lbAnimating = false;
    return;
  }
  _lbClearAnimClasses(img);
  img.classList.add("anim-prev");

  setTimeout(() => {
    lbAnimating = false;
    openLightbox(entry.idx, true);
  }, 250);
}

/**
 * @param {MouseEvent} [e]
 */
export function closeLightbox(e) {
  /** @type {any} */
  const win = window;
  // Tear down any in-progress face edit BEFORE the backdrop-click guard.
  // The guard rejects closes where e.target ≠ the lightbox backdrop, but
  // we still want the edit-mode document listeners detached even if the
  // close itself is a no-op — otherwise _lbEdit + its handlers leak
  // across photo navigation / X-button / programmatic-close paths.
  // _lbEndFaceEdit() is idempotent.
  _lbEndFaceEdit();
  if (e && e.target !== document.getElementById("lightbox")) return;
  const returnIdx = win.lightboxIdx;
  lbResetZoom();
  win.hideCardCtxMenu?.();
  const vid = /** @type {HTMLVideoElement | null} */ (document.getElementById("lb-video"));
  if (vid) {
    vid.pause();
    vid.removeAttribute("src");
  }
  document.querySelectorAll(".lb-face-overlay").forEach((el) => el.remove());
  document.querySelectorAll(".lb-face-assign-picker").forEach((el) => el.remove());
  // Defer GPS import to avoid bundling it on every load: drain any document-level listeners that pickers /
  // menus left registered. Each registered cleanup removes its
  // own listeners + the overlay; iterating over a copy because
  // each cleanup mutates `_lbActiveCleanups` via `.delete(self)`.
  for (const cleanup of [..._lbActiveCleanups]) cleanup();
  document.getElementById("lightbox")?.classList.remove("visible");
  // Mobile bottom sheet starts collapsed on next open — clear any
  // expanded state from the closing photo.
  document.getElementById("lightbox-panel")?.classList.remove("expanded");
  win.lightboxIdx = -1;
  lbAnimating = false;
  lbUndoStack = [];
  saveNavState();
  _highlightGalleryCard(returnIdx);
  // Also clear any inline updateCardInPlace flicker
  void updateCardInPlace;
}

// Mobile bottom-sheet toggle. The CSS hides this on desktop; the
// handle is only tappable at phone widths. Single source of truth
// for the expanded/collapsed state — no separate JS variable to
// drift out of sync with the DOM.
export function _lbTogglePanel() {
  document.getElementById("lightbox-panel")?.classList.toggle("expanded");
}

/**
 * @param {number} idx
 */
export function _highlightGalleryCard(idx) {
  if (idx < 0 || !vgrid) return;
  const scrollEl = document.querySelector(".content");
  if (!scrollEl || !vgrid.rowHeight || !vgrid.cols) return;
  const row = Math.floor(idx / vgrid.cols);
  const targetTop = row * vgrid.rowHeight - scrollEl.clientHeight / 2 + vgrid.rowHeight / 2;
  scrollEl.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
  setTimeout(() => {
    const card = document.querySelector(`[data-idx="${idx}"]`);
    if (!card) return;
    card.classList.add("lb-return-highlight");
    setTimeout(() => card.classList.remove("lb-return-highlight"), 2000);
  }, 350);
}
