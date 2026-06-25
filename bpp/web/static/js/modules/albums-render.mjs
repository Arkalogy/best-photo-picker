// @ts-check
/**
 * Album sidebar tree rendering — the 460-LOC renderAlbumNav function
 * that builds the entire #album-list HTML from state.albumList,
 * state.faceClusters, and the various filter / sort / nav-folder
 * preferences.
 *
 * Extracted from albums.mjs during the v0.1 cleanup. Pure render —
 * no state mutation outside DOM, all preferences read via the
 * exported helpers from albums.mjs.
 *
 * Re-exported from albums.mjs.
 */

import { authedSrc } from "./api-client.mjs";
import { _getFaceGroups } from "./groups.mjs";
import { getSectionError, safeRenderNav } from "./sidebar-safety.mjs";
import { state } from "./state.mjs";
import { _getTimelineFilter } from "./timeline.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { petDisplayName } from "./pets.mjs";
import { smartAlbumIcon, smartAlbumTip } from "./albums-render-helpers.mjs";
import {
  _SORT_LABEL,
  _albumsCollapsed,
  _albumsFilter,
  _albumsSort,
  _installNavOpenPersistence,
  _navFolderOpenAttr,
} from "./albums.mjs";


export function renderAlbumNav() {
  // Protection B: last line of defense. If anything inside _doRender
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
  const ICONS = win.ICONS || {};
  const albums = /** @type {any[]} */ (win.albumList || []);
  const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
  const petClusters = /** @type {any[]} */ (win.petClusters || []);
  const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const FACE_MIN_PHOTOS = win.FACE_MIN_PHOTOS ?? 4;
  const isClusterExcluded = /** @type {(c: any) => boolean} */ (
    win.isClusterExcluded || (() => false)
  );
  const personDisplayName = /** @type {(id: any) => string | null} */ (
    win.personDisplayName || (() => null)
  );

  /** @type {Record<string, number>} */
  const typeOrder = {
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
  const sorted = [...albums].sort((a, b) => {
    const ta = typeOrder[a.album_type] ?? 5;
    const tb = typeOrder[b.album_type] ?? 5;
    if (ta !== tb) return ta - tb;
    return a.name.localeCompare(b.name);
  });

  const allAlbums = sorted.filter((a) => a.album_type === "all");
  const manualAlbums = sorted.filter((a) => a.album_type === "manual");
  const smartAlbums = sorted.filter((a) => {
    if (!a.album_type.startsWith("smart_")) return false;
    if (a.album_type === "smart_person" && a.rule && faceClusters.length > 0) {
      const cluster = faceClusters.find((c) => c.cluster_id === a.rule.cluster_id);
      if (cluster && isClusterExcluded(cluster)) return false;
    }
    return true;
  });

  let html = "";

  const libActive =
    win.currentView === "library" ||
    win.currentView === "favorites" ||
    (win.currentView === "album" && allAlbums.some((a) => a.id === win.currentAlbumId));
  for (const album of allAlbums) {
    const active = libActive && win.currentView !== "favorites" ? " active" : "";
    const picksActive = win.currentView === "picks" ? " active" : "";
    const picksCount = album.selected_count || 0;
    html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}">
      <span><span class="nav-icon-svg">${ICONS.library || ""}</span>Library</span>
      <span class="nav-count">${album.photo_count || 0}</span>
    </div>
    <div class="nav-subitem nav-subitem-picks${picksActive}" data-action="navigateToLibraryPicks" title="Best K photos across the full library">
      <span><span class="nav-icon-svg">${ICONS.picks || ""}</span>BPP Picks</span>
      <span class="nav-count">${picksCount || ""}</span>
    </div>`;
  }
  if (allAlbums.length === 0) {
    html += `<div class="nav-item" data-action="switchToLibrary">
      <span><span class="nav-icon-svg">${ICONS.library || ""}</span>Library</span>
      <span class="nav-count">0</span>
    </div>`;
  }

  const peopleActive = win.currentView === "people" ? " active" : "";
  html += `<div class="nav-item${peopleActive}" data-action="navigateToPeople">
    <span><span class="nav-icon-svg">${ICONS.people || ""}</span>Faces</span>
  </div>`;
  // Protection B: render an inline error pill if the faces loader
  // failed. The nav-item above stays clickable; this pill is the
  // "couldn't load, retry" affordance.
  const facesErr = getSectionError("faces");
  if (facesErr) {
    html += `<div class="nav-item nav-item-error" title="${escapeAttr(facesErr.message)}" data-action="_retrySectionLoader" data-arg0="faces">
      <span class="nav-error-icon">⚠</span>
      <span class="nav-error-label">${esc(facesErr.message)}</span>
      <span class="nav-error-retry">Retry</span>
    </div>`;
  }

  if (petClusters.length > 0 || win.petsAvailable) {
    const petsActive = win.currentView === "pets" ? " active" : "";
    const petCount = petClusters.reduce((sum, c) => sum + c.photo_count, 0);
    html += `<div class="nav-item${petsActive}" data-action="navigateToPets">
      <span><span class="nav-icon-svg">${ICONS.paw || ""}</span>Pets</span>
      ${petCount > 0 ? `<span class="nav-count">${petCount}</span>` : ""}
    </div>`;
  }

  const _faceGroups = _getFaceGroups();
  if (_faceGroups.length > 0) {
    const groupsActive = win.currentView === "groups" ? " active" : "";
    html += `<div class="nav-item${groupsActive}" data-action="navigateToGroups">
      <span><span class="nav-icon-svg">${ICONS.group || ""}</span>Groups</span>
      <span class="nav-count">${_faceGroups.length}</span>
    </div>`;
  }

  const _tags = /** @type {any[]} */ (win._getTags?.() || []);
  if (_tags.length > 0) {
    const tagsActive = win.currentView === "tags" || win.currentView === "tag-photos" ? " active" : "";
    html += `<div class="nav-item${tagsActive}" data-action="navigateToTags">
      <span><span class="nav-icon-svg">${ICONS.tag || ""}</span>Tags</span>
      <span class="nav-count">${_tags.length}</span>
    </div>`;
  }

  const mapActive = win.currentView === "map" ? " active" : "";
  html += `<div class="nav-item${mapActive}" data-action="navigateToMap">
    <span><span class="nav-icon-svg">${ICONS.map || ICONS.calendar || ""}</span>Map</span>
  </div>`;

  const calActive = win.currentView === "calendar" ? " active" : "";
  html += `<div class="nav-item${calActive}" data-action="navigateToCalendar">
    <span><span class="nav-icon-svg">${ICONS.calendar || ""}</span>Calendar</span>
  </div>`;

  html += `<div class="nav-group-header"><span>Favorites</span></div>`;
  const favActive = win.currentView === "favorites" ? " active" : "";
  html += `<div class="nav-item${favActive}" data-action="navigateToFavorites">
    <span><span class="nav-icon-svg">${ICONS.heart || ""}</span>Favorites</span>
    <span class="nav-count">${favorites.size || 0}</span>
  </div>`;

  const sortMode = _albumsSort();
  const filterQ = _albumsFilter();

  // Sort button cycles through sort modes — shown inline in header
  const collapsed = _albumsCollapsed();
  html += `<details class="nav-albums-section"${collapsed ? "" : " open"} data-nav-key="section:albums">
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
      out += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}" style="padding-left:${12 + indent * 16}px"
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

  if (smartAlbums.length > 0) {
    html += `<div class="nav-group-header"><span>Smart Albums</span></div>`;
    const faceAlbums = smartAlbums.filter((a) => a.album_type === "smart_person");
    const timeAlbums = smartAlbums.filter((a) => a.album_type === "smart_time");
    const groupAlbums = smartAlbums.filter((a) => a.album_type === "smart_group");
    const namedPetAlbums = smartAlbums.filter(
      (a) =>
        a.album_type === "smart_pet" &&
        a.rule &&
        a.rule.cluster_id != null &&
        petDisplayName(a.rule.cluster_id)
    );
    const namedPetIds = new Set(namedPetAlbums.map((a) => a.id));
    const tagAlbums = smartAlbums.filter((a) => a.album_type === "smart_tag");
    const otherSmart = smartAlbums.filter(
      (a) =>
        a.album_type !== "smart_person" &&
        a.album_type !== "smart_time" &&
        a.album_type !== "smart_group" &&
        a.album_type !== "smart_tag" &&
        !namedPetIds.has(a.id)
    );

    for (const album of otherSmart) {
      const icon = smartAlbumIcon(album.album_type, ICONS);
      const active =
        win.currentAlbumId === album.id && win.currentView === "album" ? " active" : "";
      const tip = smartAlbumTip(album.album_type);
      html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
        ${tip ? `title="${escapeAttr(tip)}"` : ""}
        data-oncontextmenu="showSmartAlbumMenu" data-arg0="${album.id}" data-arg1="${escapeJsAttr(album.name)}">
        <span><span class="nav-icon-svg">${icon || ""}</span>${esc(album.name)}</span>
        <span class="nav-count">${album.photo_count || 0}</span>
      </div>`;
    }

    if (faceAlbums.length > 0 || namedPetAlbums.length > 0) {
      const faceOpen = [...faceAlbums, ...namedPetAlbums].some(
        (a) => a.id === win.currentAlbumId && win.currentView === "album"
      )
        ? " open"
        : "";
      const sortKey = win.sidebarFaceSort || "count";
      const sortedFaces = [...faceAlbums].sort((a, b) => {
        if (sortKey === "name") return a.name.localeCompare(b.name);
        return (b.photo_count || 0) - (a.photo_count || 0);
      });
      const visibleFaces = sortedFaces.filter(
        (a) =>
          (a.photo_count || 0) >= FACE_MIN_PHOTOS ||
          personDisplayName(a.rule && a.rule.cluster_id)
      );
      const hiddenCount = sortedFaces.length - visibleFaces.length;
      const sortIcon = sortKey === "name" ? "A→Z" : "#";
      html += `<details class="nav-folder"${_navFolderOpenAttr("section:faces", !!faceOpen)} data-nav-key="section:faces">
        <summary><span class="nav-icon-svg">${ICONS.people || ""}</span>Faces <span class="nav-count nav-folder-count">${visibleFaces.length + namedPetAlbums.length}</span>
          <button class="nav-sort-btn" data-prevent-default="true" title="Sort by ${sortKey === "name" ? "photo count" : "name"}">${sortIcon}</button>
        </summary>`;
      for (const album of visibleFaces) {
        const active =
          win.currentAlbumId === album.id && win.currentView === "album" ? " active" : "";
        const cluster =
          album.rule && faceClusters.find((c) => c.cluster_id === album.rule.cluster_id);
        const rep = cluster && cluster.representative;
        const thumb =
          rep && rep.thumb_hash
            ? `<img class="nav-face-thumb" src="${authedSrc(`/api/v1/faces/crop/${escapeAttr(rep.thumb_hash)}/${rep.face_index}`)}" loading="lazy">`
            : `<span class="nav-face-thumb nav-face-placeholder">${ICONS.people || ""}</span>`;
        const cid = album.rule && album.rule.cluster_id;
        html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="${cid != null ? 'showPersonCtxMenu' : 'showSmartAlbumMenu'}" data-arg0="${cid != null ? cid : album.id}" data-arg1="${escapeJsAttr(album.name)}">
          <span class="nav-face-label">${thumb}<span class="nav-face-name">${esc(album.name)}</span></span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
      }
      if (hiddenCount > 0) {
        html += `<div class="nav-item nav-show-more" data-action="navigateTo" data-arg0="people">
          <span class="nav-face-label"><span class="nav-face-thumb nav-face-placeholder">…</span><span class="nav-face-name">${hiddenCount} more</span></span>
        </div>`;
      }
      for (const album of namedPetAlbums) {
        const active =
          win.currentAlbumId === album.id && win.currentView === "album" ? " active" : "";
        const petCluster = petClusters.find((c) => c.cluster_id === album.rule.cluster_id);
        const petRep = petCluster && petCluster.representative;
        const petThumb =
          petRep && petRep.thumb_hash
            ? `<img class="nav-face-thumb" src="${authedSrc(`/api/v1/pets/crop/${escapeAttr(petRep.thumb_hash)}/${petRep.detection_index}`)}" loading="lazy">`
            : `<span class="nav-face-thumb nav-face-placeholder">${ICONS.paw || ""}</span>`;
        html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="showPetCtxMenu" data-arg0="${album.rule.cluster_id}">
          <span class="nav-face-label">${petThumb}<span class="nav-face-name">${esc(album.name)}</span></span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
      }
      html += `<div class="nav-face-boost" id="nav-face-boost">
        <div class="nav-face-boost-chips" id="nav-face-boost-chips"></div>
        <div class="slider-row nav-boost-slider">
          <label title="Make Auto-pick favor your favorite people.&#10;Click faces above to choose who matters — their photos get this much of a head start when the app picks the best shots.&#10;0 = treat everyone equally · 0.5 = strongly prefer them">People boost</label>
          <input type="range" min="0" max="0.50" step="0.01" value="0.15" data-param="face_selection_boost">
          <span class="val">0.15</span>
        </div>
      </div>`;
      html += `</details>`;
    }

    if (groupAlbums.length > 0) {
      const groupOpen = groupAlbums.some(
        (a) => a.id === win.currentAlbumId && win.currentView === "album"
      )
        ? " open"
        : "";
      html += `<details class="nav-folder"${_navFolderOpenAttr("section:groups", !!groupOpen)} data-nav-key="section:groups">
        <summary><span class="nav-icon-svg">${ICONS.group || ""}</span>Groups <span class="nav-count nav-folder-count">${groupAlbums.length}</span></summary>`;
      for (const album of groupAlbums) {
        const active =
          win.currentAlbumId === album.id && win.currentView === "album" ? " active" : "";
        html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="showSmartAlbumMenu" data-arg0="${album.id}" data-arg1="${escapeJsAttr(album.name)}">
          <span>${esc(album.name)}</span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
      }
      html += `</details>`;
    }

    if (tagAlbums.length > 0) {
      const tagOpen = tagAlbums.some(
        (a) => a.id === win.currentAlbumId && win.currentView === "album"
      )
        ? " open"
        : "";
      html += `<details class="nav-folder"${_navFolderOpenAttr("section:tags", !!tagOpen)} data-nav-key="section:tags">
        <summary><span class="nav-icon-svg">${ICONS.tag || ""}</span>Tags <span class="nav-count nav-folder-count">${tagAlbums.length}</span></summary>`;
      for (const album of tagAlbums) {
        const active =
          win.currentAlbumId === album.id && win.currentView === "album" ? " active" : "";
        const tagId = album.rule && album.rule.tag_id;
        html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="showTagAlbumMenu" data-arg0="${album.id}" data-arg1="${escapeJsAttr(album.name)}" data-arg2="${tagId || 0}">
          <span><span class="nav-icon-svg">${ICONS.tag || ""}</span>${esc(album.name)}</span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
      }
      html += `</details>`;
    }

    if (timeAlbums.length > 0) {
      const timeOpen = timeAlbums.some(
        (a) => a.id === win.currentAlbumId && win.currentView === "album"
      )
        ? " open"
        : "";
      html += `<details class="nav-folder"${_navFolderOpenAttr("section:timeline", !!timeOpen)} data-nav-key="section:timeline">
        <summary><span class="nav-icon-svg">${ICONS.calendar || ""}</span>Timeline <span class="nav-count nav-folder-count">${timeAlbums.length}</span></summary>`;
      for (const album of timeAlbums) {
        const active =
          win.currentAlbumId === album.id && win.currentView === "album" && !_getTimelineFilter()
            ? " active"
            : "";
        const isYear = album.rule && album.rule.year;
        if (isYear) {
          const yearKey = `year:${album.rule.year}`;
          const yearOpen = _navFolderOpenAttr(
            yearKey,
            win.currentAlbumId === album.id && win.currentView === "album",
          );
          html += `<details class="nav-folder nav-year-folder"${yearOpen} data-nav-key="${yearKey}" data-year="${escapeAttr(album.rule.year)}" data-album-id="${album.id}" data-ontoggle="loadYearMonths">
            <summary class="nav-item${active}" data-prevent-default="true" data-action="switchAlbum" data-arg0="${album.id}">
              <span>${esc(album.name)}</span>
              <span class="nav-item-right"><span class="nav-count">${album.photo_count || 0}</span></span>
            </summary>
            <div class="nav-year-months" id="year-months-${escapeAttr(album.rule.year)}"></div>
          </details>`;
        } else {
          html += `<div class="nav-item${active}" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}">
            <span>${esc(album.name)}</span>
            <span class="nav-count">${album.photo_count || 0}</span>
          </div>`;
        }
      }
      html += `</details>`;
    }
  }

  html += `<div class="nav-group-header" style="margin-top:12px"><span></span></div>`;

  const hidActive = win.currentView === "hidden" ? " active" : "";
  html += `<div class="nav-item${hidActive}" data-action="navigateToHidden">
    <span><span class="nav-icon-svg">${ICONS.hidden || ""}</span>Hidden</span>
    <span class="nav-count" id="hidden-count"></span>
  </div>`;

  const delActive = win.currentView === "deleted" ? " active" : "";
  html += `<div class="nav-item${delActive}" data-action="navigateToDeleted">
    <span><span class="nav-icon-svg">${ICONS.trash || ""}</span>Recently Deleted</span>
    <span class="nav-count" id="deleted-count"></span>
  </div>`;

  container.innerHTML = html;

  // The rebuild just emptied #nav-face-boost-chips and reset the boost
  // slider to its markup default — repopulate both. Without this, any
  // renderAlbumNav after the last loadAlbumFaces left the People-boost
  // pill chipless (user: "no have") and the slider showing 0.15
  // regardless of the saved setting. Same listener/state-vs-innerHTML-
  // rebuild class as the map-Expand and tuning-slider bugs.
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
