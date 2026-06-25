// @ts-check
/**
 * Trash & Hidden views, plus the per-card context menu and
 * delete/restore/enhance/hide bulk-op API helpers.
 *
 * Reads many shared globals via `window` (`currentAlbumId`, `albumList`,
 * `currentView`, `currentViewId`, `photos`, `favorites`, `overrides`,
 * `multiSelected`, `faceClusters`, `_albumPickerFilepaths`, `ICONS`)
 * and calls cross-file helpers (`renderAlbumNav`, `hide`, `show`,
 * `updateToolbarTitle`, `updateToolbarForView`, `clearMultiSelect`,
 * `loadPhotosAndRecompute`, `loadHiddenPhotos`, `setOverride`,
 * `batchAddToAlbum`) the same way — they all still live in classic land.
 *
 * Helpers from existing modules (`toggleFavorite` from toolbar.mjs,
 * `_iphShowTagPicker` from inspector.mjs, `showBatchRenameModal` from
 * batch-rename.mjs) are also looked up on `window` since they're
 * window-bridged at module load time.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * @param {string[]} filepaths
 */
export async function deletePhotos(filepaths) {
  /** @type {any} */
  const win = window;
  if (!filepaths || filepaths.length === 0) return;
  const label = filepaths.length === 1 ? "this photo" : `${filepaths.length} photos`;
  const sub =
    filepaths.length === 1
      ? "It will be moved to Recently Deleted."
      : "They will be moved to Recently Deleted.";
  const ok = await appConfirm(`Delete ${label}?`, sub);
  if (!ok) return;
  try {
    await apiFetch("/api/v1/photos/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    toast(`Deleted ${filepaths.length} photo${filepaths.length > 1 ? "s" : ""}`);
    win.clearMultiSelect?.();
    await reloadCurrentView();
  } catch (e) {
    toastError("delete the photo", e);
  }
}

/**
 * @param {string} filepath
 */
export async function deleteFromCard(filepath) {
  /** @type {any} */
  const win = window;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = win.currentAlbumId ? albums.find((a) => a.id === win.currentAlbumId) : null;
  if (album && album.album_type === "manual") {
    const ok = await appConfirm(
      `Remove from "${album.name}"?`,
      "The photo will stay in your library."
    );
    if (!ok) return;
    await removeFromAlbum([filepath]);
  } else {
    await deletePhotos([filepath]);
  }
}

export async function batchDelete() {
  /** @type {any} */
  const win = window;
  const ms = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const fps = [...ms];
  if (fps.length === 0) return;
  await deletePhotos(fps);
}



/**
 * @param {string[]} filepaths
 */
export async function removeFromAlbum(filepaths) {
  /** @type {any} */
  const win = window;
  if (!win.currentAlbumId) return;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = albums.find((a) => a.id === win.currentAlbumId);
  if (!album || album.album_type !== "manual") return;
  try {
    await apiFetch(`/api/v1/albums/${win.currentAlbumId}/remove-photos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    toast(`Removed ${filepaths.length} photo${filepaths.length > 1 ? "s" : ""} from album`);
    win.clearMultiSelect?.();
    await reloadCurrentView();
  } catch (e) {
    toastError("remove the photo", e);
  }
}

export async function navigateToDeleted() {
  /** @type {any} */
  const win = window;
  win.currentView = "deleted";
  win.currentViewId = null;
  win.currentAlbumId = null;
  win.renderAlbumNav?.();
  win.hide?.("people-view");
  win.hide?.("pets-view");
  win.hide?.("groups-view");
  win.hide?.("tags-view");
  win.hide?.("map-view");
  win.hide?.("calendar-view");
  win.hide?.("photo-grid");
  win.hide?.("tuning-controls");
  win.hide?.("timeline-bar");
  win.updateToolbarTitle?.("Recently Deleted", "");
  win.updateToolbarForView?.();
  await loadDeletedPhotos();
}

export async function loadDeletedPhotos() {
  /** @type {any} */
  const win = window;
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  try {
    // Endpoint is paginated (limit=200 default). 200 is plenty for
    // the typical Recently-Deleted view; if a user has more, the
    // grid will show the most-recently-deleted 200 and the badge
    // shows the real total. A future scroll-to-load can advance
    // offset using the response's total/limit/offset triple.
    const resp = await apiFetch("/api/v1/photos/deleted?limit=200");
    const photos = /** @type {any[]} */ (resp.photos || []);
    const total = resp.total ?? photos.length;

    const countEl = document.getElementById("deleted-count");
    if (countEl) countEl.textContent = total ? String(total) : "";
    win.updateToolbarTitle?.(
      "Recently Deleted",
      total > 0 ? `${total} photo${total !== 1 ? "s" : ""}` : ""
    );

    const ICONS = win.ICONS || {};
    if (photos.length === 0) {
      grid.innerHTML = `<div class="empty-state"><div class="icon">${ICONS.trash || ""}</div>
        <div class="title">No deleted photos</div>
        <div class="desc">Photos you delete will appear here for 30 days before being permanently removed.</div>
      </div>`;
      win.show?.("photo-grid");
      return;
    }

    const now = new Date();
    let html = '<div class="deleted-toolbar">';
    html += '<button class="batch-btn" data-action="restoreAllDeleted">Restore All</button>';
    html +=
      '<button class="batch-btn batch-exclude" data-action="permanentDeleteAll">Delete All</button>';
    html += "</div>";
    for (const p of photos) {
      const deletedDate = new Date(p.deleted_at + "Z");
      const daysAgo = Math.floor((now.getTime() - deletedDate.getTime()) / 86400000);
      const daysLeft = Math.max(0, 30 - daysAgo);
      const countdownText =
        daysLeft === 0 ? "Expiring soon" : `${daysLeft} day${daysLeft !== 1 ? "s" : ""} left`;
      const thumbSrc = p.thumb_hash ? authedSrc(`/thumb/${p.thumb_hash}`) : "";
      html += `<div class="deleted-card" data-filepath="${escapeAttr(p.filepath)}" data-oncontextmenu="showCardCtxMenu" data-arg0="${escapeJsAttr(p.filepath)}">
        <div class="card-image">
          ${thumbSrc ? `<img src="${thumbSrc}" alt="${escapeAttr(p.filename)}">` : '<div style="height:var(--thumb-height,220px);background:var(--surface-hover)"></div>'}
          <div class="deleted-countdown-badge">${countdownText}</div>
          <div class="deleted-hover-actions">
            <button class="deleted-hover-btn restore" data-stop-propagation="true" data-action="restorePhotos" data-arg0="['${escapeJsAttr(p.filepath)}']">Restore</button>
            <button class="deleted-hover-btn remove" data-stop-propagation="true" data-action="permanentDeletePhotos" data-arg0="['${escapeJsAttr(p.filepath)}']">Delete</button>
          </div>
        </div>
        <div class="card-info">
          <div class="card-name">${esc(p.filename)}</div>
        </div>
      </div>`;
    }
    grid.innerHTML = html;
    win.show?.("photo-grid");
  } catch (e) {
    toastError("load deleted photos", e);
  }
}

/**
 * @param {string[]} filepaths
 */
export async function restorePhotos(filepaths) {
  try {
    await apiFetch("/api/v1/photos/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    toast(`Restored ${filepaths.length} photo${filepaths.length > 1 ? "s" : ""}`);
    await loadDeletedPhotos();
  } catch (e) {
    toastError("restore the photo", e);
  }
}

/**
 * @param {string[]} filepaths
 */
export async function permanentDeletePhotos(filepaths) {
  const label = filepaths.length === 1 ? "this photo" : `${filepaths.length} photos`;
  const ok = await appConfirm(
    `Permanently delete ${label}?`,
    "This cannot be undone. Files will be removed from disk."
  );
  if (!ok) return;
  try {
    await apiFetch("/api/v1/photos/delete-permanent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths, confirmation: "delete" }),
    });
    toast(`Permanently deleted ${filepaths.length} photo${filepaths.length > 1 ? "s" : ""}`);
    await loadDeletedPhotos();
  } catch (e) {
    toastError("delete the photo", e);
  }
}

export async function restoreAllDeleted() {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  const cards = /** @type {NodeListOf<HTMLElement>} */ (
    grid.querySelectorAll(".deleted-card[data-filepath]")
  );
  const filepaths = [...cards].map((c) => c.dataset.filepath || "").filter(Boolean);
  if (filepaths.length === 0) return;
  await restorePhotos(filepaths);
}

export async function permanentDeleteAll() {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  const cards = /** @type {NodeListOf<HTMLElement>} */ (
    grid.querySelectorAll(".deleted-card[data-filepath]")
  );
  const filepaths = [...cards].map((c) => c.dataset.filepath || "").filter(Boolean);
  if (filepaths.length === 0) return;
  await permanentDeletePhotos(filepaths);
}

export async function reloadCurrentView() {
  /** @type {any} */
  const win = window;
  if (win.currentView === "deleted") {
    await loadDeletedPhotos();
  } else if (win.currentView === "hidden") {
    await loadHiddenPhotos();
  } else if (
    win.currentView === "album" ||
    win.currentView === "library" ||
    win.currentView === "favorites"
  ) {
    await win.loadPhotosAndRecompute?.();
  }
}

/**
 * @param {string[]} filepaths
 */
export async function hidePhotos(filepaths) {
  /** @type {any} */
  const win = window;
  if (!filepaths || filepaths.length === 0) return;
  try {
    await apiFetch("/api/v1/photos/hide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    toast(`Hidden ${filepaths.length} photo${filepaths.length > 1 ? "s" : ""}`);
    win.clearMultiSelect?.();
    await reloadCurrentView();
  } catch (e) {
    toastError("hide the photo", e);
  }
}

/**
 * @param {string[]} filepaths
 */
export async function unhidePhotos(filepaths) {
  /** @type {any} */
  const win = window;
  if (!filepaths || filepaths.length === 0) return;
  try {
    await apiFetch("/api/v1/photos/unhide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
    toast(`Unhidden ${filepaths.length} photo${filepaths.length > 1 ? "s" : ""}`);
    if (win.currentView === "hidden") {
      await loadHiddenPhotos();
    } else {
      await reloadCurrentView();
    }
  } catch (e) {
    toastError("unhide the photo", e);
  }
}

export async function batchHide() {
  /** @type {any} */
  const win = window;
  const ms = /** @type {Set<string>} */ (win.multiSelected || new Set());
  const fps = [...ms];
  if (fps.length === 0) return;
  await hidePhotos(fps);
}

export async function navigateToHidden() {
  /** @type {any} */
  const win = window;
  win.currentView = "hidden";
  win.currentViewId = null;
  win.currentAlbumId = null;
  win.renderAlbumNav?.();
  win.hide?.("people-view");
  win.hide?.("pets-view");
  win.hide?.("groups-view");
  win.hide?.("map-view");
  win.hide?.("calendar-view");
  win.hide?.("photo-grid");
  win.hide?.("tuning-controls");
  win.hide?.("timeline-bar");
  win.updateToolbarTitle?.("Hidden", "");
  win.updateToolbarForView?.();
  await loadHiddenPhotos();
}

export async function loadHiddenPhotos() {
  /** @type {any} */
  const win = window;
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  try {
    // Paginated like /photos/deleted (limit=200 default). Badge shows
    // the true total; grid renders the first page.
    const resp = await apiFetch("/api/v1/photos/hidden?limit=200");
    const hiddenPhotos = /** @type {any[]} */ (resp.photos || []);
    const total = resp.total ?? hiddenPhotos.length;

    const countEl = document.getElementById("hidden-count");
    if (countEl) countEl.textContent = total ? String(total) : "";
    win.updateToolbarTitle?.(
      "Hidden",
      total > 0 ? `${total} photo${total !== 1 ? "s" : ""}` : ""
    );

    const ICONS = win.ICONS || {};
    if (hiddenPhotos.length === 0) {
      grid.innerHTML = `<div class="empty-state"><div class="icon">${ICONS.hidden || ""}</div>
        <div class="title">No hidden photos</div>
        <div class="desc">Photos you hide will appear here. They won't show in your library or albums.</div>
      </div>`;
      win.show?.("photo-grid");
      return;
    }

    let html = '<div class="deleted-toolbar">';
    html += '<button class="batch-btn" data-action="unhideAllHidden">Unhide All</button>';
    html += "</div>";
    for (const p of hiddenPhotos) {
      const thumbSrc = p.thumb_hash ? authedSrc(`/thumb/${p.thumb_hash}`) : "";
      html += `<div class="deleted-card" data-filepath="${escapeAttr(p.filepath)}" data-oncontextmenu="showCardCtxMenu" data-arg0="${escapeJsAttr(p.filepath)}">
        <div class="card-image">
          ${thumbSrc ? `<img src="${thumbSrc}" alt="${escapeAttr(p.filename)}">` : '<div style="height:var(--thumb-height,220px);background:var(--surface-hover)"></div>'}
          <div class="deleted-hover-actions">
            <button class="deleted-hover-btn restore" data-stop-propagation="true" data-action="unhidePhotos" data-arg0="['${escapeJsAttr(p.filepath)}']">Unhide</button>
          </div>
        </div>
        <div class="card-info">
          <div class="card-name">${esc(p.filename)}</div>
        </div>
      </div>`;
    }
    grid.innerHTML = html;
    win.show?.("photo-grid");
  } catch (e) {
    toastError("load hidden photos", e);
  }
}

export async function unhideAllHidden() {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  const cards = /** @type {NodeListOf<HTMLElement>} */ (
    grid.querySelectorAll(".deleted-card[data-filepath]")
  );
  const filepaths = [...cards].map((c) => c.dataset.filepath || "").filter(Boolean);
  if (filepaths.length === 0) return;
  await unhidePhotos(filepaths);
}

// Card context menu moved to deleted-ctx-menu.mjs in the v0.1 cleanup.
// Enhance/revert/batch-enhance moved to deleted-enhance.mjs.
// Re-exported below so back-compat (window-bridged data-action handlers,
// tests that import from deleted.mjs) keeps working.
import {
  _getCardCtxFilepath,
  _resetCtxMenuState,
  hideCardCtxMenu,
  initCardCtxMenu,
  showCardCtxMenu,
} from "./deleted-ctx-menu.mjs";
export {
  _getCardCtxFilepath,
  hideCardCtxMenu,
  initCardCtxMenu,
  showCardCtxMenu,
};

/** Test-only: reset module-private state (re-exposes the moved
 *  card-ctx-menu reset so existing tests keep working). */
export function _resetDeletedState() {
  _resetCtxMenuState();
}

import {
  batchEnhance,
  enhancePhotos,
  revertEnhance,
} from "./deleted-enhance.mjs";
export { batchEnhance, enhancePhotos, revertEnhance };
