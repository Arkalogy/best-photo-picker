// @ts-check
/**
 * Album sidebar tree rendering — builds the entire #album-list HTML
 * from the window globals (albumList, faceClusters, …, captured once
 * per render as a NavCtx) and the various filter / sort / nav-folder
 * preferences.
 *
 * Extracted from albums.mjs during the v0.1 cleanup. Pure render —
 * no state mutation outside DOM, all preferences read via the
 * exported helpers from albums.mjs. The sidebar is rendered top to
 * bottom as sections, each a function returning its HTML; the Smart
 * Albums sections live in albums-render-smart.mjs.
 *
 * Re-exported from albums.mjs.
 */

import { _getFaceGroups } from "./groups.mjs";
import { getSectionError, safeRenderNav } from "./sidebar-safety.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { renderSmartAlbums } from "./albums-render-smart.mjs";
import {
  _SORT_LABEL,
  _albumsCollapsed,
  _albumsFilter,
  _albumsSort,
  _installNavOpenPersistence,
  _navFolderOpenAttr,
} from "./albums.mjs";

/**
 * What every sidebar section reads: the window globals, captured once
 * per render.
 * @typedef {object} NavCtx
 * @property {any} win
 * @property {Record<string, string>} ICONS
 * @property {any[]} faceClusters
 * @property {any[]} petClusters
 * @property {Set<string>} favorites
 * @property {number} FACE_MIN_PHOTOS
 * @property {(id: any) => string | null} personDisplayName
 */

/** @type {Record<string, number>} */
const _TYPE_ORDER = {
  all: 0,
  manual: 1,
  smart_score: 2,
  smart_recent: 3,
  smart_edited: 4,
  smart_unsorted: 5,
  smart_video: 6,
  smart_screenshot: 7,
  smart_moments: 7.5,
  smart_duplicates: 8,
  smart_no_faces: 9,
  smart_document: 10,
  smart_pet: 11,
  smart_group: 12,
  smart_hidden: 13,
  smart_time: 14,
  smart_person: 15,
  smart_tag: 16,
};

/**
 * Where an album sits in the sidebar: the position the server sent with
 * it (the registry's, a plugin type's included), else the built-in
 * table, else the middle.
 * @param {any} album
 * @returns {number}
 */
export function albumSortKey(album) {
  if (typeof album?.sort_order === "number") return album.sort_order;
  return _TYPE_ORDER[album?.album_type] ?? 5;
}

export function renderAlbumNav() {
  // Sidebar isolation: last line of defense. If anything inside _doRender
  // throws (data shape mismatch, missing window global, plugin
  // rendering a broken section), substitute an inline error pill
  // with a Reload button instead of leaving the sidebar empty.
  safeRenderNav(_doRender);
}

function _doRender() {
  /** @type {any} */
  const win = window;
  const container = document.getElementById("album-list");
  if (!container) return;
  const sidebar = document.querySelector(".sidebar");
  if (sidebar) _installNavOpenPersistence(/** @type {HTMLElement} */ (sidebar));
  /** @type {NavCtx} */
  const ctx = {
    win,
    ICONS: win.ICONS || {},
    faceClusters: /** @type {any[]} */ (win.faceClusters || []),
    petClusters: /** @type {any[]} */ (win.petClusters || []),
    favorites: /** @type {Set<string>} */ (win.favorites || new Set()),
    FACE_MIN_PHOTOS: win.FACE_MIN_PHOTOS ?? 4,
    personDisplayName: /** @type {(id: any) => string | null} */ (
      win.personDisplayName || (() => null)
    ),
  };
  const isClusterExcluded = /** @type {(c: any) => boolean} */ (
    win.isClusterExcluded || (() => false)
  );

  const albums = /** @type {any[]} */ (win.albumList || []);
  const sorted = [...albums].sort((a, b) => {
    const ta = albumSortKey(a);
    const tb = albumSortKey(b);
    if (ta !== tb) return ta - tb;
    return a.name.localeCompare(b.name);
  });
  const allAlbums = sorted.filter((a) => a.album_type === "all");
  const manualAlbums = sorted.filter((a) => a.album_type === "manual");
  const smartAlbums = sorted.filter((a) => {
    if (!a.album_type.startsWith("smart_")) return false;
    if (a.album_type === "smart_person" && a.rule && ctx.faceClusters.length > 0) {
      const cluster = ctx.faceClusters.find((c) => c.cluster_id === a.rule.cluster_id);
      if (cluster && isClusterExcluded(cluster)) return false;
    }
    return true;
  });

  container.innerHTML =
    _renderLibraryAndViews(ctx, allAlbums) +
    _renderManualAlbums(ctx, manualAlbums) +
    renderSmartAlbums(ctx, smartAlbums) +
    _renderHiddenAndDeleted(ctx);

  _restoreFaceBoost();
}

/**
 * Library, BPP Picks, Faces, Pets, Groups, Tags, Map, Calendar, Favorites.
 * @param {NavCtx} ctx
 * @param {any[]} allAlbums
 * @returns {string}
 */
function _renderLibraryAndViews(ctx, allAlbums) {
  const { win, ICONS } = ctx;
  let html = "";

  const libActive =
    win.currentView === "library" ||
    win.currentView === "favorites" ||
    (win.currentView === "album" && allAlbums.some((a) => a.id === win.currentAlbumId));
  for (const album of allAlbums) {
    const active = libActive && win.currentView !== "favorites" ? " active" : "";
    const picksActive = win.currentView === "picks" ? " active" : "";
    const picksCount = album.selected_count || 0;
    html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}">
      <span><span class="nav-icon-svg">${ICONS.library || ""}</span>Library</span>
      <span class="nav-count">${album.photo_count || 0}</span>
    </div>
    <div class="nav-subitem nav-subitem-picks${picksActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToLibraryPicks" title="Best K photos across the full library">
      <span><span class="nav-icon-svg">${ICONS.picks || ""}</span>BPP Picks</span>
      <span class="nav-count">${picksCount || ""}</span>
    </div>`;
  }
  if (allAlbums.length === 0) {
    html += `<div class="nav-item" role="button" tabindex="0" data-key-activate="true" data-action="switchToLibrary">
      <span><span class="nav-icon-svg">${ICONS.library || ""}</span>Library</span>
      <span class="nav-count">0</span>
    </div>`;
  }

  const peopleActive = win.currentView === "people" ? " active" : "";
  html += `<div class="nav-item${peopleActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToPeople">
    <span><span class="nav-icon-svg">${ICONS.people || ""}</span>Faces</span>
  </div>`;
  // Render an inline error pill if the faces loader
  // failed. The nav-item above stays clickable; this pill is the
  // "couldn't load, retry" affordance.
  const facesErr = getSectionError("faces");
  if (facesErr) {
    html += `<div class="nav-item nav-item-error" role="button" tabindex="0" data-key-activate="true" title="${escapeAttr(facesErr.message)}" data-action="_retrySectionLoader" data-arg0="faces">
      <span class="nav-error-icon">⚠</span>
      <span class="nav-error-label">${esc(facesErr.message)}</span>
      <span class="nav-error-retry">Retry</span>
    </div>`;
  }

  if (ctx.petClusters.length > 0 || win.petsAvailable) {
    const petsActive = win.currentView === "pets" ? " active" : "";
    const petCount = ctx.petClusters.reduce((sum, c) => sum + c.photo_count, 0);
    html += `<div class="nav-item${petsActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToPets">
      <span><span class="nav-icon-svg">${ICONS.paw || ""}</span>Pets</span>
      ${petCount > 0 ? `<span class="nav-count">${petCount}</span>` : ""}
    </div>`;
  }

  const _faceGroups = _getFaceGroups();
  if (_faceGroups.length > 0) {
    const groupsActive = win.currentView === "groups" ? " active" : "";
    html += `<div class="nav-item${groupsActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToGroups">
      <span><span class="nav-icon-svg">${ICONS.group || ""}</span>Groups</span>
      <span class="nav-count">${_faceGroups.length}</span>
    </div>`;
  }

  const _tags = /** @type {any[]} */ (win._getTags?.() || []);
  if (_tags.length > 0) {
    const tagsActive = win.currentView === "tags" || win.currentView === "tag-photos" ? " active" : "";
    html += `<div class="nav-item${tagsActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToTags">
      <span><span class="nav-icon-svg">${ICONS.tag || ""}</span>Tags</span>
      <span class="nav-count">${_tags.length}</span>
    </div>`;
  }

  const mapActive = win.currentView === "map" ? " active" : "";
  html += `<div class="nav-item${mapActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToMap">
    <span><span class="nav-icon-svg">${ICONS.map || ICONS.calendar || ""}</span>Map</span>
  </div>`;

  const calActive = win.currentView === "calendar" ? " active" : "";
  html += `<div class="nav-item${calActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToCalendar">
    <span><span class="nav-icon-svg">${ICONS.calendar || ""}</span>Calendar</span>
  </div>`;

  html += `<div class="nav-group-header"><span>Favorites</span></div>`;
  const favActive = win.currentView === "favorites" ? " active" : "";
  html += `<div class="nav-item${favActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToFavorites">
    <span><span class="nav-icon-svg">${ICONS.heart || ""}</span>Favorites</span>
    <span class="nav-count">${ctx.favorites.size || 0}</span>
  </div>`;
  return html;
}

/**
 * The collapsible "Albums" section: sort / filter controls and the
 * manual album tree.
 * @param {NavCtx} ctx
 * @param {any[]} manualAlbums
 * @returns {string}
 */
function _renderManualAlbums(ctx, manualAlbums) {
  const { win, ICONS } = ctx;
  const sortMode = _albumsSort();
  const filterQ = _albumsFilter();

  // Sort button cycles through sort modes — shown inline in header
  const collapsed = _albumsCollapsed();
  let html = `<details class="nav-albums-section"${collapsed ? "" : " open"} data-nav-key="section:albums">
    <summary class="nav-group-header nav-albums-header">
      <span><span class="nav-albums-chevron">▸</span>Albums</span>
      <span class="nav-albums-controls">
        <span class="nav-albums-sort-btn" data-stop-propagation="true" data-action="cycleAlbumsSort"
          title="Sort albums — click to change"
        >${_SORT_LABEL[sortMode]}</span>
        <button id="add-album-btn" data-stop-propagation="true" data-action="showNewAlbumInput"
          title="New album" aria-label="Create new album">+</button>
      </span>
    </summary>`;

  if (manualAlbums.length >= 5) {
    html += `<div class="nav-albums-filter-wrap">
      <input type="search" class="nav-albums-filter" placeholder="Filter…"
        value="${escapeAttr(filterQ)}" data-oninput="albumFilterInput"
        aria-label="Filter albums">
    </div>`;
  }

  // Sort manual albums
  let sortedManual = [...manualAlbums];
  if (sortMode === "name-asc") sortedManual.sort((a, b) => a.name.localeCompare(b.name));
  else if (sortMode === "name-desc") sortedManual.sort((a, b) => b.name.localeCompare(a.name));
  else if (sortMode === "count-desc") sortedManual.sort((a, b) => (b.photo_count || 0) - (a.photo_count || 0));
  else if (sortMode === "count-asc") sortedManual.sort((a, b) => (a.photo_count || 0) - (b.photo_count || 0));
  else if (sortMode === "date-desc") sortedManual.sort((a, b) => (b.id || 0) - (a.id || 0));

  const topAlbums = sortedManual.filter(
    (a) =>
      !a.parent_id &&
      (!filterQ || a.name.toLowerCase().includes(filterQ.toLowerCase()))
  );
  /** @type {Record<number, any[]>} */
  const childrenOf = {};
  for (const a of manualAlbums) {
    if (a.parent_id) {
      (childrenOf[a.parent_id] = childrenOf[a.parent_id] || []).push(a);
    }
  }

  /** @param {any} album */
  function picksBadge(album) {
    const sc = album.selected_count || 0;
    if (sc <= 0) return "";
    return `<span class="nav-picks-badge" title="${sc} picked by BPP">✓${sc}</span>`;
  }

  /**
   * @param {any} album
   * @param {number} indent
   * @returns {string}
   */
  function renderAlbumItem(album, indent) {
    const active = win.currentAlbumId === album.id && win.currentView === "album" ? " active" : "";
    const actions = `<span class="nav-item-actions"><button data-stop-propagation="true" data-action="deleteAlbumPrompt" data-arg0="${album.id}" data-arg1="${escapeJsAttr(album.name)}" title="Delete">&times;</button></span>`;
    const badge = picksBadge(album);
    const children = childrenOf[album.id];
    let out = "";
    if (children && children.length > 0) {
      const hasActiveChild = children.some(
        (c) => c.id === win.currentAlbumId && win.currentView === "album"
      );
      const isCurrent = win.currentAlbumId === album.id && win.currentView === "album";
      const isOpen = _navFolderOpenAttr(`album:${album.id}`, hasActiveChild || isCurrent);
      out += `<details class="nav-folder album-folder"${isOpen} data-nav-key="album:${album.id}">
        <summary class="nav-item${active}" data-prevent-default="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}" style="padding-left:${12 + indent * 16}px"
          data-ondblclick="_bppDblclickToggle"
          data-oncontextmenu="showAlbumMoveMenu" data-arg0="${album.id}">
          <span><span class="nav-icon-svg">${ICONS.folder || ""}</span>${esc(album.name)}</span>
          <span class="nav-item-right">${badge}<span class="nav-count">${album.photo_count || 0}</span>${actions}</span>
        </summary>`;
      for (const child of children) {
        out += renderAlbumItem(child, indent + 1);
      }
      out += `</details>`;
    } else {
      out += `<div class="nav-item${active}" role="group" aria-roledescription="album" aria-label="${escapeAttr(album.name)}, Enter opens" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}" style="padding-left:${12 + indent * 16}px"
        data-oncontextmenu="showAlbumMoveMenu" data-arg0="${album.id}">
        <span><span class="nav-icon-svg">${ICONS.folder || ""}</span>${esc(album.name)}</span>
        <span class="nav-item-right">${badge}<span class="nav-count">${album.photo_count || 0}</span>${actions}</span>
      </div>`;
    }
    return out;
  }

  for (const album of topAlbums) {
    html += renderAlbumItem(album, 0);
  }
  if (manualAlbums.length === 0) {
    html += `<div class="nav-empty-hint">No albums yet</div>`;
  } else if (topAlbums.length === 0 && filterQ) {
    html += `<div class="nav-empty-hint">No match</div>`;
  }
  html += `</details>`;
  return html;
}

/**
 * @param {NavCtx} ctx
 * @returns {string}
 */
function _renderHiddenAndDeleted(ctx) {
  const { win, ICONS } = ctx;
  let html = `<div class="nav-group-header" style="margin-top:12px"><span></span></div>`;

  const hidActive = win.currentView === "hidden" ? " active" : "";
  html += `<div class="nav-item${hidActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToHidden">
    <span><span class="nav-icon-svg">${ICONS.hidden || ""}</span>Hidden</span>
    <span class="nav-count" id="hidden-count"></span>
  </div>`;

  const delActive = win.currentView === "deleted" ? " active" : "";
  html += `<div class="nav-item${delActive}" role="button" tabindex="0" data-key-activate="true" data-action="navigateToDeleted">
    <span><span class="nav-icon-svg">${ICONS.trash || ""}</span>Recently Deleted</span>
    <span class="nav-count" id="deleted-count"></span>
  </div>`;
  return html;
}

/**
 * The rebuild just emptied #nav-face-boost-chips and reset the boost
 * slider to its markup default — repopulate both. Without this, any
 * renderAlbumNav after the last loadAlbumFaces left the People-boost
 * pill chipless (user: "no have") and the slider showing 0.15
 * regardless of the saved setting. Same listener/state-vs-innerHTML-
 * rebuild class as the map-Expand and tuning-slider bugs.
 */
function _restoreFaceBoost() {
  /** @type {any} */ (window).renderFaceGallery?.();
  const boostInput = /** @type {HTMLInputElement | null} */ (
    document.querySelector('#nav-face-boost [data-param="face_selection_boost"]')
  );
  const boostVal = /** @type {any} */ (window).state_defaults?.face_selection_boost;
  if (boostInput && boostVal !== undefined) {
    boostInput.value = String(boostVal);
    const sib = /** @type {HTMLElement | null} */ (boostInput.nextElementSibling);
    if (sib) sib.textContent = String(boostVal);
  }
}
