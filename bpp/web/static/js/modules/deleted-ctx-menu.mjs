// @ts-check
/**
 * Per-card right-click / long-press context menu.
 *
 * Extracted from deleted.mjs during the v0.1 cleanup. Owns the menu
 * shown when a user right-clicks a grid card — favorite/include/
 * exclude/enhance/tag/hide/delete/restore/permanent-delete. Touch
 * long-press is reinterpreted as multi-select.
 *
 * Re-exported from deleted.mjs.
 */

import { esc } from "./text-format.mjs";
import { toast } from "./toast.mjs";
import {
  deleteFromCard,
  enhancePhotos,
  hidePhotos,
  permanentDeletePhotos,
  restorePhotos,
  revertEnhance,
  unhidePhotos,
} from "./deleted.mjs";

let cardCtxFilepath = /** @type {string | null} */ (null);

/**
 * @param {MouseEvent} e
 * @param {string} filepath
 */
export function showCardCtxMenu(e, filepath) {
  /** @type {any} */
  const win = window;
  e.preventDefault();
  e.stopPropagation();

  const pe = /** @type {any} */ (e);
  const isTouch = "ontouchstart" in window && (!e.button || pe.pointerType === "touch");
  if (isTouch) {
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    const multiSelected =
      /** @type {Set<string>} */ (win.multiSelected || (win.multiSelected = new Set()));
    const idx = items.findIndex((p) => p && p.filepath === filepath);
    if (idx >= 0) {
      if (multiSelected.has(filepath)) multiSelected.delete(filepath);
      else multiSelected.add(filepath);
      win.lastMultiClickIdx = idx;
      win.updateMultiSelectUI?.();
    }
    return;
  }

  cardCtxFilepath = filepath;
  const menu = document.getElementById("card-ctx-menu");
  if (!menu) return;
  const photos = /** @type {any[]} */ (win.photos || []);
  const photo = photos.find((p) => p.filepath === filepath);
  const isDeleted = win.currentView === "deleted" || (photo && photo.deleted_at);

  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const isFav = favorites.has(filepath);
  /** @param {string} id @param {string} label */
  const setLabel = (id, label) => {
    const el = document.getElementById(id);
    if (!el) return;
    const shortcut = el.querySelector(".ctx-shortcut");
    const shortcutHtml = shortcut ? shortcut.outerHTML : "";
    el.innerHTML = shortcutHtml ? label + " " + shortcutHtml : label;
  };
  setLabel("card-ctx-fav", isFav ? "Unfavorite" : "Favorite");

  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const curOverride = overrides[filepath] || null;
  setLabel("card-ctx-include", curOverride === "include" ? "Clear Override" : "Force Include");
  setLabel("card-ctx-exclude", curOverride === "exclude" ? "Clear Override" : "Force Exclude");

  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = win.currentAlbumId ? albums.find((a) => a.id === win.currentAlbumId) : null;
  const inManualAlbum = album && album.album_type === "manual";
  const deleteEl = document.getElementById("card-ctx-delete");
  if (deleteEl) {
    setLabel("card-ctx-delete", inManualAlbum ? "Remove from Album" : "Delete");
    deleteEl.classList.toggle("danger", !inManualAlbum);
  }

  const isHidden = win.currentView === "hidden" || (photo && photo.hidden_at);
  const isSpecial = isDeleted || isHidden;

  const blockOrNone = isSpecial ? "none" : "block";
  /** @param {string} id @param {string} display */
  const setDisplay = (id, display) => {
    const el = /** @type {HTMLElement | null} */ (document.getElementById(id));
    if (el) el.style.display = display;
  };
  setDisplay("card-ctx-fav", blockOrNone);
  setDisplay("card-ctx-sep1", blockOrNone);
  setDisplay("card-ctx-include", blockOrNone);
  setDisplay("card-ctx-exclude", blockOrNone);
  setDisplay("card-ctx-sep2", blockOrNone);
  const isEnhanced = photo && photo._enhanced;
  setDisplay("card-ctx-enhance", isSpecial ? "none" : isEnhanced ? "none" : "block");
  setDisplay("card-ctx-revert", isSpecial ? "none" : isEnhanced ? "block" : "none");

  const tagEl = document.getElementById("card-ctx-tag");
  const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
  if (tagEl) tagEl.style.display = isSpecial || faceClusters.length === 0 ? "none" : "block";
  setDisplay("card-ctx-hide", blockOrNone);
  setDisplay("card-ctx-unhide", isHidden ? "block" : "none");
  setDisplay("card-ctx-delete", blockOrNone);
  setDisplay("card-ctx-restore", isDeleted ? "block" : "none");
  setDisplay("card-ctx-perm-delete", isDeleted ? "block" : "none");

  const mx = e.clientX;
  const my = e.clientY;
  menu.classList.remove("hidden");
  const mw = menu.offsetWidth;
  const mh = menu.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  /** @type {HTMLElement} */ (menu).style.left = (mx + mw > vw ? vw - mw - 4 : mx) + "px";
  /** @type {HTMLElement} */ (menu).style.top = (my + mh > vh ? vh - mh - 4 : my) + "px";
}

export function hideCardCtxMenu() {
  const menu = document.getElementById("card-ctx-menu");
  if (menu) menu.classList.add("hidden");
  cardCtxFilepath = null;
}

export function initCardCtxMenu() {
  /** @type {any} */
  const win = window;
  document.addEventListener("click", () => hideCardCtxMenu());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { hideCardCtxMenu(); return; }
    if (!cardCtxFilepath) return;
    const menu = document.getElementById("card-ctx-menu");
    if (!menu || menu.classList.contains("hidden")) return;
    const key = e.key.toLowerCase();
    const item = /** @type {HTMLElement | null} */ (
      [...menu.querySelectorAll(".ctx-menu-item[data-key]")].find(
        (el) => /** @type {HTMLElement} */ (el).dataset.key === key &&
                 /** @type {HTMLElement} */ (el).style.display !== "none"
      ) || null
    );
    if (!item) return;
    e.preventDefault();
    item.click();
  });
  const menu = document.getElementById("card-ctx-menu");
  if (!menu) return;
  menu.addEventListener("click", async (e) => {
    const target = /** @type {HTMLElement | null} */ (e.target);
    const item = target?.closest(".ctx-menu-item");
    if (!item || !cardCtxFilepath) return;
    const action = /** @type {HTMLElement} */ (item).dataset.action;
    const fp = cardCtxFilepath;
    hideCardCtxMenu();

    if (action === "favorite") {
      win.toggleFavorite?.(fp);
    } else if (action === "include") {
      win.setOverride?.(fp, "include");
    } else if (action === "exclude") {
      win.setOverride?.(fp, "exclude");
    } else if (action === "add-to-album") {
      win._albumPickerFilepaths = [fp];
      const list = document.getElementById("album-picker-list");
      const albums = /** @type {any[]} */ (win.albumList || []);
      const manualAlbums = albums.filter((a) => a.album_type === "manual");
      if (list) {
        if (manualAlbums.length === 0) {
          list.innerHTML =
            '<div class="album-picker-empty">No albums yet. Create one below.</div>';
        } else {
          list.innerHTML = manualAlbums
            .map(
              (a) =>
                `<div class="album-picker-item" data-action="batchAddToAlbum" data-arg0="${a.id}">
            <span>${esc(a.name)}</span>
            <span class="album-picker-count">${a.photo_count} photos</span>
          </div>`
            )
            .join("");
        }
      }
      const newName = /** @type {HTMLInputElement | null} */ (
        document.getElementById("album-picker-new-name")
      );
      if (newName) newName.value = "";
      document.getElementById("album-picker-overlay")?.classList.add("visible");
    } else if (action === "tag-person") {
      const photos = /** @type {any[]} */ (win.photos || []);
      const photo = photos.find((p) => p.filepath === fp);
      if (photo && photo.thumb_hash && win._iphShowTagPicker) {
        win._iphShowTagPicker(e, photo.thumb_hash);
      } else {
        toast("Cannot tag: photo has no thumbnail", true);
      }
    } else if (action === "enhance") {
      enhancePhotos([fp]);
    } else if (action === "revert-enhance") {
      revertEnhance([fp]);
    } else if (action === "rename") {
      win.showBatchRenameModal?.();
    } else if (action === "hide") {
      hidePhotos([fp]);
    } else if (action === "unhide") {
      unhidePhotos([fp]);
    } else if (action === "delete") {
      deleteFromCard(fp);
    } else if (action === "restore") {
      restorePhotos([fp]);
    } else if (action === "perm-delete") {
      permanentDeletePhotos([fp]);
    }
  });
}

/** Test-only: read internal cardCtxFilepath. */
export function _getCardCtxFilepath() {
  return cardCtxFilepath;
}

/** Test-only: reset internal state. */
export function _resetCtxMenuState() {
  cardCtxFilepath = null;
}
