// @ts-check
/**
 * Batch operations on multi-selected photos: override/favorite/album.
 *
 * Extracted from photos.mjs during the v0.1 cleanup. Owns the
 * 'apply X to N selected' path that fires from the multi-select
 * action bar:
 *
 *   * batchOverride(mode)     — set include/exclude on N photos
 *   * batchFavorite(favorite) — toggle favorite on N photos
 *   * showAlbumPickerModal / hideAlbumPicker — modal lifecycle
 *   * batchAddToAlbum(albumId) / createAlbumAndAdd — destinations
 *   * updateOverrideStats     — stats counter refresh
 *
 * Re-exported from photos.mjs.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { esc } from "./text-format.mjs";
import { saveOverrides } from "./toolbar.mjs";
import { scheduleRecompute } from "./analysis.mjs";
import { showToast, toastError } from "./toast.mjs";
import { renderAlbumNav } from "./albums.mjs";
import { clearMultiSelect, renderGrid, vgrid } from "./photos.mjs";


export async function batchOverride(mode) {
  /** @type {any} */
  const win = window;
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const filepaths = Array.from(multiSelected);
  if (filepaths.length === 0) return;
  /** @type {Record<string, string | null>} */
  const prevOverrides = {};
  for (const fp of filepaths) prevOverrides[fp] = overrides[fp] || null;
  for (const fp of filepaths) {
    if (mode) overrides[fp] = mode;
    else delete overrides[fp];
  }
  saveOverrides();
  scheduleRecompute();
  clearMultiSelect();
  const url = win.currentAlbumId
    ? `/api/v1/albums/${win.currentAlbumId}/batch/override`
    : "/api/v1/batch/override";
  try {
    await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths, mode }),
    });
  } catch (e) {
    toastError("apply the override to the selected photos", e);
  }
  const label =
    mode === "include"
      ? `Included ${filepaths.length} photos`
      : mode === "exclude"
        ? `Excluded ${filepaths.length} photos`
        : `Cleared overrides on ${filepaths.length} photos`;
  showToast(label, 5000, () => {
    for (const fp of filepaths) {
      if (prevOverrides[fp]) overrides[fp] = prevOverrides[fp];
      else delete overrides[fp];
    }
    saveOverrides();
    scheduleRecompute();
    /** @type {Record<string, string[]>} */
    const byMode = {};
    for (const fp of filepaths) {
      const m = prevOverrides[fp] || "null";
      (byMode[m] = byMode[m] || []).push(fp);
    }
    for (const [m, fps] of Object.entries(byMode)) {
      apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepaths: fps, mode: m === "null" ? null : m }),
      }).catch((e) => console.warn("Batch override undo failed:", e));
    }
  });
}

/**
 * @param {boolean} favorite
 */
export async function batchFavorite(favorite) {
  /** @type {any} */
  const win = window;
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const filepaths = Array.from(multiSelected);
  if (filepaths.length === 0) return;
  /** @type {Record<string, boolean>} */
  const prevFavs = {};
  for (const fp of filepaths) prevFavs[fp] = favorites.has(fp);
  for (const fp of filepaths) {
    if (favorite) favorites.add(fp);
    else favorites.delete(fp);
  }
  renderGrid();
  clearMultiSelect();
  const url = "/api/v1/batch/favorite";
  try {
    await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths, favorite }),
    });
  } catch (e) {
    toastError("favorite the selected photos", e);
  }
  const label = favorite
    ? `Favorited ${filepaths.length} photos`
    : `Unfavorited ${filepaths.length} photos`;
  showToast(label, 5000, () => {
    for (const fp of filepaths) {
      if (prevFavs[fp]) favorites.add(fp);
      else favorites.delete(fp);
    }
    renderGrid();
    const toFav = filepaths.filter((fp) => prevFavs[fp]);
    const toUnfav = filepaths.filter((fp) => !prevFavs[fp]);
    if (toFav.length)
      apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepaths: toFav, favorite: true }),
      }).catch((e) => console.warn("Batch favorite undo failed:", e));
    if (toUnfav.length)
      apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepaths: toUnfav, favorite: false }),
      }).catch((e) => console.warn("Batch favorite undo failed:", e));
  });
}

export function showAlbumPickerModal() {
  /** @type {any} */
  const win = window;
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const albums = /** @type {any[]} */ (win.albumList || []);
  win._albumPickerFilepaths = Array.from(multiSelected);
  if (win._albumPickerFilepaths.length === 0) return;
  const list = document.getElementById("album-picker-list");
  if (!list) return;
  const manualAlbums = albums.filter((a) => a.album_type === "manual");
  if (manualAlbums.length === 0) {
    list.innerHTML = '<div class="album-picker-empty">No albums yet. Create one below.</div>';
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
  const newName = /** @type {HTMLInputElement | null} */ (
    document.getElementById("album-picker-new-name")
  );
  if (newName) newName.value = "";
  document.getElementById("album-picker-overlay")?.classList.add("visible");
}

export function hideAlbumPicker() {
  /** @type {any} */
  const win = window;
  document.getElementById("album-picker-overlay")?.classList.remove("visible");
  win._albumPickerFilepaths = [];
}

/**
 * @param {number} albumId
 */
export async function batchAddToAlbum(albumId) {
  /** @type {any} */
  const win = window;
  const filepaths = /** @type {string[]} */ (win._albumPickerFilepaths || []);
  if (filepaths.length === 0) return;
  hideAlbumPicker();
  clearMultiSelect();
  try {
    const data = await apiFetch(`/api/v1/albums/${albumId}/add-photos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    const albums = /** @type {any[]} */ (win.albumList || []);
    const album = albums.find((a) => a.id === albumId);
    const name = album ? album.name : `Album ${albumId}`;
    showToast(`Added ${data.count} photos to "${name}"`);
    const resp = await apiFetch("/api/v1/albums");
    if (resp.albums) {
      win.albumList = resp.albums;
      win.renderAlbumNav?.();
    }
  } catch (e) {
    toastError("add the photos to the album", e);
  }
}

export async function createAlbumAndAdd() {
  /** @type {any} */
  const win = window;
  const input = /** @type {HTMLInputElement | null} */ (
    document.getElementById("album-picker-new-name")
  );
  const name = (input?.value || "").trim();
  if (!name) return;
  const filepaths = /** @type {string[]} */ (win._albumPickerFilepaths || []);
  if (filepaths.length === 0) return;
  hideAlbumPicker();
  clearMultiSelect();
  try {
    const createResp = await apiFetch("/api/v1/albums", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const albumId = createResp.id;
    const addResp = await apiFetch(`/api/v1/albums/${albumId}/add-photos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    showToast(`Created "${name}" with ${addResp.count} photos`);
    const resp = await apiFetch("/api/v1/albums");
    if (resp.albums) {
      win.albumList = resp.albums;
      win.renderAlbumNav?.();
    }
  } catch (e) {
    toastError("create the album", e);
  }
}

export function updateOverrideStats() {
  /** @type {any} */
  const win = window;
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const inc = Object.values(overrides).filter((v) => v === "include").length;
  const exc = Object.values(overrides).filter((v) => v === "exclude").length;
  const el = document.getElementById("override-stats");
  if (!el) return;
  if (inc + exc === 0) el.textContent = "None";
  else el.textContent = `${inc} included, ${exc} excluded`;
}
