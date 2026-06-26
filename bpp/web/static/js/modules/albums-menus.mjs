// @ts-check
/**
 * Album context menus, rename/delete prompts, year/month nav,
 * and minor sidebar handlers.
 *
 * Extracted from albums.mjs during the v0.1 cleanup. Owns the
 * 'right-click an album' surface plus a few related dialogs:
 *
 *   * showNewAlbumInput / deleteAlbumPrompt
 *   * loadYearMonths / switchToMonth
 *   * toggleFaceSort
 *   * showAlbumMoveMenu / moveAlbumTo  — drag-to-folder helpers
 *   * showSmartAlbumMenu / renameSmartAlbum / removeSmartAlbum
 *   * showTagAlbumMenu / removeTagAlbum
 *
 * Re-exported from albums.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { appConfirm, appPrompt } from "./dialogs.mjs";
import { esc, escapeJsAttr } from "./text-format.mjs";
import { showToast, toast, toastError } from "./toast.mjs";
import { saveSetting } from "./settings-client.mjs";
import { _getTimelineFilter, _setTimelineFilter } from "./timeline.mjs";
import { MONTHS_SHORT } from "./date-format.mjs";
import { renderGrid } from "./photos.mjs";
import { _albumFiltersMap, LS_ALBUM_FILTERS } from "./albums.mjs";
import { loadAlbumList, renderAlbumNav, switchAlbum } from "./albums.mjs";


export async function showNewAlbumInput() {
  const name = await appPrompt("New album", { placeholder: "Album name", okLabel: "Create" });
  if (!name) return;
  try {
    const data = await apiFetch("/api/v1/albums", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (data.error) {
      toast(data.error, true);
      return;
    }
    await loadAlbumList();
    switchAlbum(data.id);
  } catch (e) {
    console.warn("Create album failed:", e);
    toastError("create the album", e);
  }
}

/**
 * @param {number} albumId
 * @param {string} name
 */
export async function deleteAlbumPrompt(albumId, name) {
  /** @type {any} */
  const win = window;
  const ok = await appConfirm(
    `Delete "${name}"?`,
    "This album will be removed. Photos inside it are not deleted.",
    { okLabel: "Delete", okClass: "danger" }
  );
  if (!ok) return;
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, { method: "DELETE" });
    // Drop any per-scope filter the user had set on this album so the
    // entry doesn't linger in localStorage and silently apply if SQLite
    // later reuses the album ID for a different album.
    const map = _albumFiltersMap();
    const key = `album:${albumId}`;
    if (key in map) {
      delete map[key];
      try {
        localStorage.setItem(LS_ALBUM_FILTERS, JSON.stringify(map));
      } catch (e) {
        console.warn("Failed to prune album filter entry:", e);
      }
    }
    if (win.currentAlbumId === albumId) {
      const albums = /** @type {any[]} */ (win.albumList || []);
      const allAlbum = albums.find((a) => a.album_type === "all");
      if (allAlbum) switchAlbum(allAlbum.id);
    }
    await loadAlbumList();
  } catch (e) {
    console.warn("Delete album failed:", e);
    toastError("delete the album", e);
  }
}

/**
 * @param {HTMLElement} detailsEl
 */
export async function loadYearMonths(detailsEl) {
  /** @type {any} */
  const win = window;
  if (!(/** @type {HTMLDetailsElement} */ (detailsEl).open)) return;
  const year = detailsEl.dataset.year;
  const container = /** @type {HTMLElement | null} */ (
    detailsEl.querySelector(".nav-year-months")
  );
  if (!container || container.dataset.loaded) return;
  try {
    const data = await apiFetch(`/api/v1/albums/time/months?year=${year}`);
    const albumId = parseInt(detailsEl.dataset.albumId || "0", 10);
    let html = "";
    for (const m of data.months) {
      const mo = String(m.month).padStart(2, "0");
      const filterVal = `${year}-${mo}`;
      const active =
        win.currentAlbumId === albumId && _getTimelineFilter() === filterVal ? " active" : "";
      html += `<div class="nav-item nav-month-item${active}" data-action="switchToMonth" data-arg0="${albumId}" data-arg1="${filterVal}">
        <span>${MONTHS_SHORT[m.month - 1]}</span>
        <span class="nav-count">${m.count}</span>
      </div>`;
    }
    container.innerHTML = html;
    container.dataset.loaded = "1";
  } catch (e) {
    // Sidebar drawer expand — failure means the months list stays
    // empty. Console-only because toasting on every failed timeline
    // load would be noisy and the user can just collapse + retry.
    console.warn("Load year months failed:", e);
  }
}

/**
 * @param {number} albumId
 * @param {string} monthFilter
 */
export function switchToMonth(albumId, monthFilter) {
  /** @type {any} */
  const win = window;
  _setTimelineFilter(monthFilter);
  if (win.currentAlbumId === albumId && win.currentView === "album") {
    renderGrid();
    renderAlbumNav();
  } else {
    switchAlbum(albumId).then(() => {
      _setTimelineFilter(monthFilter);
      renderGrid();
      renderAlbumNav();
    });
  }
}

export function toggleFaceSort() {
  /** @type {any} */
  const win = window;
  win.sidebarFaceSort = win.sidebarFaceSort === "count" ? "name" : "count";
  saveSetting("sidebar_face_sort", win.sidebarFaceSort);
  renderAlbumNav();
}

/**
 * @param {MouseEvent} event
 * @param {number} albumId
 */
export function showAlbumMoveMenu(event, albumId) {
  /** @type {any} */
  const win = window;
  event.preventDefault();
  event.stopPropagation();
  document.getElementById("album-move-menu")?.remove();

  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = albums.find((a) => a.id === albumId);
  if (!album || album.album_type !== "manual") return;

  const manualAlbums = albums.filter((a) => a.album_type === "manual" && a.id !== albumId);
  const menu = document.createElement("div");
  menu.id = "album-move-menu";
  menu.className = "ctx-menu";

  let html = `<div class="ctx-header">Move "${esc(album.name)}"</div>`;
  if (album.parent_id) {
    html += `<div class="ctx-item" data-action="moveAlbumTo" data-arg0="${albumId}" data-arg1="null">Top level</div>`;
  }
  for (const a of manualAlbums) {
    if (a.id === album.parent_id) continue;
    if (a.parent_id === albumId) continue;
    html += `<div class="ctx-item" data-action="moveAlbumTo" data-arg0="${albumId}" data-arg1="${a.id}">${esc(a.name)}</div>`;
  }
  if (manualAlbums.length === 0 && !album.parent_id) {
    html += `<div class="ctx-item disabled">No albums to move into</div>`;
  }
  menu.innerHTML = html;

  menu.style.position = "fixed";
  menu.style.left = event.clientX + "px";
  menu.style.top = event.clientY + "px";
  menu.style.zIndex = "9999";
  document.body.appendChild(menu);

  setTimeout(() => {
    document.addEventListener(
      "click",
      function _close() {
        menu.remove();
        document.removeEventListener("click", _close);
      },
      { once: true }
    );
  }, 0);
}

/**
 * @param {number} albumId
 * @param {number | null} parentId
 */
export async function moveAlbumTo(albumId, parentId) {
  /** @type {any} */
  const win = window;
  document.getElementById("album-move-menu")?.remove();
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_id: parentId }),
    });
    await loadAlbumList();
    const albums = /** @type {any[]} */ (win.albumList || []);
    const album = albums.find((a) => a.id === albumId);
    const target = parentId ? albums.find((a) => a.id === parentId) : null;
    showToast(
      target
        ? `Moved "${album.name}" into "${target.name}"`
        : `Moved "${album.name}" to top level`
    );
  } catch (e) {
    toastError("move the album", e);
  }
}

/**
 * @param {MouseEvent} event
 * @param {number} albumId
 * @param {string} albumName
 */
export function showSmartAlbumMenu(event, albumId, albumName) {
  event.preventDefault();
  event.stopPropagation();
  document.getElementById("smart-album-ctx")?.remove();

  const menu = document.createElement("div");
  menu.id = "smart-album-ctx";
  menu.className = "ctx-menu";
  menu.innerHTML =
    `<div class="ctx-item" data-action="renameSmartAlbum" data-arg0="${albumId}" data-arg1="${escapeJsAttr(albumName)}">Rename</div>` +
    `<div class="ctx-item ctx-item-danger" data-action="removeSmartAlbum" data-arg0="${albumId}" data-arg1="${escapeJsAttr(albumName)}">Remove</div>`;
  menu.style.position = "fixed";
  menu.style.left = event.clientX + "px";
  menu.style.top = event.clientY + "px";
  menu.style.zIndex = "9999";
  document.body.appendChild(menu);

  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth)
      menu.style.left = window.innerWidth - rect.width - 8 + "px";
    if (rect.bottom > window.innerHeight)
      menu.style.top = window.innerHeight - rect.height - 8 + "px";
  });

  setTimeout(() => {
    document.addEventListener(
      "click",
      function _close() {
        menu.remove();
        document.removeEventListener("click", _close);
      },
      { once: true }
    );
  }, 0);
}

/**
 * @param {number} albumId
 * @param {string} currentName
 */
export async function renameSmartAlbum(albumId, currentName) {
  document.getElementById("smart-album-ctx")?.remove();
  const name = await appPrompt("Rename album", {
    placeholder: "Album name",
    value: currentName,
    okLabel: "Rename",
  });
  if (!name || name === currentName) return;
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await loadAlbumList();
  } catch (e) {
    toastError("rename the album", e);
  }
}

/**
 * @param {number} albumId
 * @param {string} name
 */
export async function removeSmartAlbum(albumId, name) {
  /** @type {any} */
  const win = window;
  document.getElementById("smart-album-ctx")?.remove();
  const ok = await appConfirm(
    `Remove "${name}"?`,
    "This won't delete any photos. The album won't come back on re-analysis.",
    { okLabel: "Remove", okClass: "danger" }
  );
  if (!ok) return;
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, { method: "DELETE" });
    if (win.currentAlbumId === albumId) {
      const albums = /** @type {any[]} */ (win.albumList || []);
      const allAlbum = albums.find((a) => a.album_type === "all");
      if (allAlbum) switchAlbum(allAlbum.id);
    }
    await loadAlbumList();
    toast("Album removed");
  } catch (e) {
    toastError("remove the album", e);
  }
}

/**
 * @param {MouseEvent} event
 * @param {number} albumId
 * @param {string} albumName
 * @param {number} tagId
 */
export function showTagAlbumMenu(event, albumId, albumName, tagId) {
  event.preventDefault();
  event.stopPropagation();
  document.getElementById("smart-album-ctx")?.remove();

  const menu = document.createElement("div");
  menu.id = "smart-album-ctx";
  menu.className = "ctx-menu";
  menu.innerHTML =
    `<div class="ctx-item" data-action="renameSmartAlbum" data-arg0="${albumId}" data-arg1="${escapeJsAttr(albumName)}">Rename</div>` +
    `<div class="ctx-item ctx-item-danger" data-action="removeTagAlbum" data-arg0="${albumId}" data-arg1="${escapeJsAttr(albumName)}" data-arg2="${tagId}">Delete tag</div>`;
  menu.style.position = "fixed";
  menu.style.left = event.clientX + "px";
  menu.style.top = event.clientY + "px";
  menu.style.zIndex = "9999";
  document.body.appendChild(menu);

  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth)
      menu.style.left = window.innerWidth - rect.width - 8 + "px";
    if (rect.bottom > window.innerHeight)
      menu.style.top = window.innerHeight - rect.height - 8 + "px";
  });

  setTimeout(() => {
    document.addEventListener(
      "click",
      function _close() {
        menu.remove();
        document.removeEventListener("click", _close);
      },
      { once: true }
    );
  }, 0);
}

/**
 * @param {number} albumId
 * @param {string} name
 * @param {number} tagId
 */
export async function removeTagAlbum(albumId, name, tagId) {
  /** @type {any} */
  const win = window;
  document.getElementById("smart-album-ctx")?.remove();
  const ok = await appConfirm(
    `Delete tag "${name}"?`,
    "This will untag all photos with this label. Photos themselves are not affected.",
    { okLabel: "Delete tag", okClass: "danger" }
  );
  if (!ok) return;
  try {
    if (tagId) await apiFetch(`/api/v1/tags/${tagId}`, { method: "DELETE" });
    if (win.currentAlbumId === albumId) {
      const albums = /** @type {any[]} */ (win.albumList || []);
      const allAlbum = albums.find((a) => a.album_type === "all");
      if (allAlbum) switchAlbum(allAlbum.id);
    }
    await loadAlbumList();
    toast("Tag deleted");
  } catch (e) {
    toastError("delete the tag", e);
  }
}
