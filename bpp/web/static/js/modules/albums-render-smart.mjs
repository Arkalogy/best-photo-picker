// @ts-check
/**
 * The "Smart Albums" part of the album sidebar: the loose smart albums,
 * then the Faces (+ named pets), Groups, Tags and Timeline folders.
 *
 * Split out of albums-render.mjs (the 500-LOC cap). Each function
 * returns the HTML for its section; `_doRender` there concatenates
 * them. A `NavCtx` (see albums-render.mjs) carries the window globals
 * every section reads.
 */

import { faceCropUrl, petCropUrl } from "./api-client.mjs";
import { _getTimelineFilter } from "./timeline.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { petDisplayName } from "./pets.mjs";
import { smartAlbumIcon, smartAlbumTip } from "./albums-render-helpers.mjs";
import { _navFolderOpenAttr } from "./albums.mjs";

/** @typedef {import("./albums-render.mjs").NavCtx} NavCtx */

/**
 * @param {NavCtx} ctx
 * @param {any} album
 */
function _isActive(ctx, album) {
  return ctx.win.currentAlbumId === album.id && ctx.win.currentView === "album";
}

/**
 * @param {NavCtx} ctx
 * @param {any[]} albums
 */
function _anyActive(ctx, albums) {
  return albums.some((a) => _isActive(ctx, a));
}

/**
 * @param {NavCtx} ctx
 * @param {any[]} smartAlbums
 * @returns {string}
 */
export function renderSmartAlbums(ctx, smartAlbums) {
  if (smartAlbums.length === 0) return "";
  let html = `<div class="nav-group-header"><span>Smart Albums</span></div>`;
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
    const icon = smartAlbumIcon(album.album_type, ctx.ICONS);
    const active = _isActive(ctx, album) ? " active" : "";
    const tip = smartAlbumTip(album.album_type);
    html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
        ${tip ? `title="${escapeAttr(tip)}"` : ""}
        data-oncontextmenu="showSmartAlbumMenu" data-arg0="${album.id}" data-arg1="${escapeAttr(album.name)}">
        <span><span class="nav-icon-svg">${icon || ""}</span>${esc(album.name)}</span>
        <span class="nav-count">${album.photo_count || 0}</span>
      </div>`;
  }

  if (faceAlbums.length > 0 || namedPetAlbums.length > 0) {
    html += _renderFacesFolder(ctx, faceAlbums, namedPetAlbums);
  }
  if (groupAlbums.length > 0) html += _renderGroupsFolder(ctx, groupAlbums);
  if (tagAlbums.length > 0) html += _renderTagsFolder(ctx, tagAlbums);
  if (timeAlbums.length > 0) html += _renderTimelineFolder(ctx, timeAlbums);
  return html;
}

/**
 * @param {NavCtx} ctx
 * @param {any[]} faceAlbums
 * @param {any[]} namedPetAlbums
 * @returns {string}
 */
function _renderFacesFolder(ctx, faceAlbums, namedPetAlbums) {
  const { win, ICONS, faceClusters, petClusters, FACE_MIN_PHOTOS, personDisplayName } = ctx;
  const faceOpen = _anyActive(ctx, [...faceAlbums, ...namedPetAlbums]) ? " open" : "";
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
  let html = `<details class="nav-folder"${_navFolderOpenAttr("section:faces", !!faceOpen)} data-nav-key="section:faces">
        <summary><span class="nav-icon-svg">${ICONS.people || ""}</span>Faces <span class="nav-count nav-folder-count">${visibleFaces.length + namedPetAlbums.length}</span>
          <button class="nav-sort-btn" data-prevent-default="true" title="Sort by ${sortKey === "name" ? "photo count" : "name"}">${sortIcon}</button>
        </summary>`;
  for (const album of visibleFaces) {
    const active = _isActive(ctx, album) ? " active" : "";
    const cluster =
      album.rule && faceClusters.find((c) => c.cluster_id === album.rule.cluster_id);
    const rep = cluster && cluster.representative;
    const thumb =
      rep && rep.thumb_hash
        ? `<img class="nav-face-thumb" src="${faceCropUrl(rep.thumb_hash, rep.face_index)}" loading="lazy">`
        : `<span class="nav-face-thumb nav-face-placeholder">${ICONS.people || ""}</span>`;
    const cid = album.rule && album.rule.cluster_id;
    html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="${cid != null ? "showPersonCtxMenu" : "showSmartAlbumMenu"}" data-arg0="${cid != null ? cid : album.id}" data-arg1="${escapeAttr(album.name)}">
          <span class="nav-face-label">${thumb}<span class="nav-face-name">${esc(album.name)}</span></span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
  }
  if (hiddenCount > 0) {
    html += `<div class="nav-item nav-show-more" role="button" tabindex="0" data-key-activate="true" data-action="navigateTo" data-arg0="people">
          <span class="nav-face-label"><span class="nav-face-thumb nav-face-placeholder">…</span><span class="nav-face-name">${hiddenCount} more</span></span>
        </div>`;
  }
  for (const album of namedPetAlbums) {
    const active = _isActive(ctx, album) ? " active" : "";
    const petCluster = petClusters.find((c) => c.cluster_id === album.rule.cluster_id);
    const petRep = petCluster && petCluster.representative;
    const petThumb =
      petRep && petRep.thumb_hash
        ? `<img class="nav-face-thumb" src="${petCropUrl(petRep.thumb_hash, petRep.detection_index)}" loading="lazy">`
        : `<span class="nav-face-thumb nav-face-placeholder">${ICONS.paw || ""}</span>`;
    html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
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
  return html;
}

/**
 * @param {NavCtx} ctx
 * @param {any[]} groupAlbums
 * @returns {string}
 */
function _renderGroupsFolder(ctx, groupAlbums) {
  const groupOpen = _anyActive(ctx, groupAlbums) ? " open" : "";
  let html = `<details class="nav-folder"${_navFolderOpenAttr("section:groups", !!groupOpen)} data-nav-key="section:groups">
        <summary><span class="nav-icon-svg">${ctx.ICONS.group || ""}</span>Groups <span class="nav-count nav-folder-count">${groupAlbums.length}</span></summary>`;
  for (const album of groupAlbums) {
    const active = _isActive(ctx, album) ? " active" : "";
    html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="showSmartAlbumMenu" data-arg0="${album.id}" data-arg1="${escapeAttr(album.name)}">
          <span>${esc(album.name)}</span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
  }
  html += `</details>`;
  return html;
}

/**
 * @param {NavCtx} ctx
 * @param {any[]} tagAlbums
 * @returns {string}
 */
function _renderTagsFolder(ctx, tagAlbums) {
  const tagOpen = _anyActive(ctx, tagAlbums) ? " open" : "";
  let html = `<details class="nav-folder"${_navFolderOpenAttr("section:tags", !!tagOpen)} data-nav-key="section:tags">
        <summary><span class="nav-icon-svg">${ctx.ICONS.tag || ""}</span>Tags <span class="nav-count nav-folder-count">${tagAlbums.length}</span></summary>`;
  for (const album of tagAlbums) {
    const active = _isActive(ctx, album) ? " active" : "";
    const tagId = album.rule && album.rule.tag_id;
    html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}"
          data-oncontextmenu="showTagAlbumMenu" data-arg0="${album.id}" data-arg1="${escapeAttr(album.name)}" data-arg2="${tagId || 0}">
          <span><span class="nav-icon-svg">${ctx.ICONS.tag || ""}</span>${esc(album.name)}</span>
          <span class="nav-count">${album.photo_count || 0}</span>
        </div>`;
  }
  html += `</details>`;
  return html;
}

/**
 * @param {NavCtx} ctx
 * @param {any[]} timeAlbums
 * @returns {string}
 */
function _renderTimelineFolder(ctx, timeAlbums) {
  const timeOpen = _anyActive(ctx, timeAlbums) ? " open" : "";
  let html = `<details class="nav-folder"${_navFolderOpenAttr("section:timeline", !!timeOpen)} data-nav-key="section:timeline">
        <summary><span class="nav-icon-svg">${ctx.ICONS.calendar || ""}</span>Timeline <span class="nav-count nav-folder-count">${timeAlbums.length}</span></summary>`;
  for (const album of timeAlbums) {
    const active = _isActive(ctx, album) && !_getTimelineFilter() ? " active" : "";
    const isYear = album.rule && album.rule.year;
    if (isYear) {
      const yearKey = `year:${album.rule.year}`;
      const yearOpen = _navFolderOpenAttr(yearKey, _isActive(ctx, album));
      html += `<details class="nav-folder nav-year-folder"${yearOpen} data-nav-key="${yearKey}" data-year="${escapeAttr(album.rule.year)}" data-album-id="${album.id}" data-ontoggle="loadYearMonths">
            <summary class="nav-item${active}" data-prevent-default="true" data-action="switchAlbum" data-arg0="${album.id}">
              <span>${esc(album.name)}</span>
              <span class="nav-item-right"><span class="nav-count">${album.photo_count || 0}</span></span>
            </summary>
            <div class="nav-year-months" id="year-months-${escapeAttr(album.rule.year)}"></div>
          </details>`;
    } else {
      html += `<div class="nav-item${active}" role="button" tabindex="0" data-key-activate="true" data-action="switchAlbum" data-arg0="${album.id}" data-album-id="${album.id}">
            <span>${esc(album.name)}</span>
            <span class="nav-count">${album.photo_count || 0}</span>
          </div>`;
    }
  }
  html += `</details>`;
  return html;
}
