// @ts-check
/**
 * Tags browse/manage view.
 *
 * A "Tags" nav entry (shown once any tag exists) opens a card grid —
 * one card per tag: cover thumb (top-scored member), name, photo count.
 *   * Click a card        → grid of that tag's photos (full cards,
 *                           lightbox-compatible via currentGridItems).
 *   * Right-click a card  → Rename / Merge into… / Delete.
 *
 * Card grid clones the Groups view pattern (#tags-view container,
 * .people-grid layout); the photo grid clones the Moments-timeline
 * pattern (vgrid parked, renderCardHTML cards into #photo-grid).
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm, appPrompt } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { renderCardHTML } from "./photos-card.mjs";
import { toast, toastError } from "./toast.mjs";

/** @type {Array<{id:number,name:string,count:number,cover_thumb_hash:string|null}>} */
let _tags = [];

export function _getTags() {
  return _tags;
}

/** Load the tag list (with counts + covers) and re-render if visible. */
export async function loadTagsList() {
  try {
    const data = await apiFetch("/api/v1/tags");
    _tags = data.tags || [];
  } catch (e) {
    console.warn("Failed to load tags:", e);
    _tags = [];
  }
  return _tags;
}

/** Render the Tags view container — empty state or one card per tag. */
export function showTagsView() {
  /** @type {any} */
  const win = window;
  win.currentAlbumId = null;

  const content = document.querySelector(".content");
  let view = /** @type {HTMLElement | null} */ (document.getElementById("tags-view"));
  if (!view) {
    view = document.createElement("div");
    view.id = "tags-view";
    view.className = "people-grid";
    content?.appendChild(view);
  }
  view.classList.remove("hidden");
  win.show?.("toolbar");
  win.show?.("status-bar");

  const ICONS = win.ICONS || {};
  const withPhotos = _tags;

  if (withPhotos.length === 0) {
    view.innerHTML = `<div class="empty-state people-empty">
      <div class="icon">${ICONS.tag || ""}</div>
      <div class="title">No Tags Yet</div>
      <div class="desc">Tag photos from the lightbox ("+ Add tag") or select several and use Batch → Tag. Tags appear here for browsing and management.</div>
    </div>`;
    win.updateToolbarTitle?.("Tags", "No tags");
    return;
  }

  view.innerHTML = withPhotos
    .map((t, idx) => {
      const cover = t.cover_thumb_hash
        ? `<img src="${authedSrc("/thumb/" + t.cover_thumb_hash)}" alt="">`
        : `<div class="tag-card-cover-empty">${ICONS.tag || ""}</div>`;
      return `<div class="person-card tag-card" data-action="navigateToTagPhotos" data-arg0="${t.id}"
      data-oncontextmenu="_tagCardCtxMenu" data-tag-idx="${idx}">
      <div class="tag-card-cover">${cover}</div>
      <div class="person-label">
        <div class="person-name" title="${escapeAttr(t.name)}">${esc(t.name)}</div>
        <div class="person-count">${t.count} photo${t.count === 1 ? "" : "s"}</div>
      </div>
    </div>`;
    })
    .join("");

  const subtitle = `${withPhotos.length} tag${withPhotos.length === 1 ? "" : "s"}`;
  win.updateToolbarTitle?.("Tags", subtitle);
  const summary = document.getElementById("status-summary");
  if (summary) summary.textContent = subtitle;
}

/** Nav entry → load + show. */
export async function navigateToTags() {
  /** @type {any} */
  const win = window;
  win.currentView = "tags";
  win.currentViewId = null;
  win.currentAlbumId = null;
  for (const v of ["photo-grid", "people-view", "pets-view", "groups-view", "map-view", "calendar-view"]) {
    win.hide?.(v);
  }
  win.hide?.("person-album-bar");
  await loadTagsList();
  showTagsView();
  win.renderAlbumNav?.();
  win.updateToolbarForView?.();
}

/**
 * Open one tag's photos as a full-card grid in #photo-grid (lightbox
 * works via currentGridItems; vgrid parked like the Moments timeline).
 * @param {number} tagId
 */
export async function navigateToTagPhotos(tagId) {
  /** @type {any} */
  const win = window;
  let data;
  try {
    data = await apiFetch(`/api/v1/tags/${tagId}/photos`);
  } catch (e) {
    toastError("open that tag", e);
    return;
  }
  const photos = /** @type {any[]} */ (data.photos || []);
  const name = data.tag?.name || "tag";

  win.currentView = "tag-photos";
  win.currentViewId = tagId;
  win.currentAlbumId = null;
  for (const v of ["people-view", "pets-view", "groups-view", "map-view", "calendar-view", "tags-view"]) {
    win.hide?.(v);
  }
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  if (win.vgrid) win.vgrid.items = [];
  win.photos = photos;
  win.currentGridItems = photos;
  grid.classList.add("simple-cards");
  grid.style.paddingTop = "0";
  grid.style.paddingBottom = "0";
  grid.innerHTML = photos.length
    ? photos.map((p, i) => renderCardHTML(p, i)).join("")
    : `<div class="empty-state"><div class="title">No photos</div>
       <div class="desc">No active photos carry this tag.</div></div>`;
  win.show?.("photo-grid");
  win.updateToolbarTitle?.(`Tag: ${name}`, `${photos.length} photo${photos.length === 1 ? "" : "s"}`);
  win.renderAlbumNav?.();
}

// ── Card context menu: Rename / Merge into… / Delete ──

/** @type {number|null} */
let _ctxTagId = null;

/**
 * data-oncontextmenu handler — `this` is the card.
 * @param {MouseEvent} e
 */
export function _tagCardCtxMenu(e) {
  e.preventDefault();
  e.stopPropagation();
  const idx = Number(/** @type {HTMLElement} */ (this).dataset.tagIdx);
  const t = _tags[idx];
  if (!t) return;
  _ctxTagId = t.id;
  let menu = document.getElementById("tag-ctx-menu");
  if (!menu) {
    menu = document.createElement("div");
    menu.id = "tag-ctx-menu";
    menu.className = "ctx-menu hidden";
    menu.innerHTML = `
      <div class="ctx-menu-item" data-action="_tagCtxRename">Rename <span class="ctx-shortcut">R</span></div>
      <div class="ctx-menu-item" data-action="_tagCtxMerge">Merge into&hellip; <span class="ctx-shortcut">M</span></div>
      <div class="ctx-menu-item danger" data-action="_tagCtxDelete">Delete <span class="ctx-shortcut">D</span></div>`;
    document.body.appendChild(menu);
    document.addEventListener("click", () => menu?.classList.add("hidden"));
    document.addEventListener("keydown", (ke) => {
      if (menu?.classList.contains("hidden") || _ctxTagId === null) return;
      const map = /** @type {Record<string, string>} */ ({
        r: "_tagCtxRename", m: "_tagCtxMerge", d: "_tagCtxDelete",
      });
      const action = map[ke.key.toLowerCase()];
      if (action) {
        ke.preventDefault();
        menu.querySelector(`[data-action="${action}"]`)?.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        menu.classList.add("hidden");
      } else if (ke.key === "Escape") {
        menu.classList.add("hidden");
      }
    });
  }
  menu.style.left = `${e.clientX}px`;
  menu.style.top = `${e.clientY}px`;
  menu.classList.remove("hidden");
}

function _ctxTag() {
  return _tags.find((t) => t.id === _ctxTagId) || null;
}

export async function _tagCtxRename() {
  const t = _ctxTag();
  if (!t) return;
  const name = await appPrompt("Rename tag", { value: t.name, okLabel: "Rename" });
  if (!name || name.trim().toLowerCase() === t.name) return;
  try {
    await apiFetch(`/api/v1/tags/${t.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    toast(`Renamed to "${name.trim().toLowerCase()}"`);
    await loadTagsList();
    showTagsView();
  } catch (e) {
    toastError(`rename "${t.name}"`, e);
  }
}

export async function _tagCtxMerge() {
  const t = _ctxTag();
  if (!t) return;
  const others = _tags.filter((x) => x.id !== t.id);
  if (!others.length) {
    toast("No other tag to merge into", true);
    return;
  }
  const name = await appPrompt(`Merge "${t.name}" into which tag?`, {
    placeholder: others.map((o) => o.name).slice(0, 5).join(", ") + "…",
    okLabel: "Merge",
  });
  if (!name) return;
  const target = others.find((o) => o.name === name.trim().toLowerCase());
  if (!target) {
    toast(`No tag named "${name.trim()}" — type an existing tag's exact name`, true);
    return;
  }
  const ok = await appConfirm(
    `Merge "${t.name}" into "${target.name}"?`,
    `${t.count} photo${t.count === 1 ? "" : "s"} will move; "${t.name}" is deleted.`,
    { okLabel: "Merge" },
  );
  if (!ok) return;
  try {
    await apiFetch(`/api/v1/tags/${t.id}/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_tag_id: target.id }),
    });
    toast(`Merged "${t.name}" into "${target.name}"`);
    await loadTagsList();
    showTagsView();
  } catch (e) {
    toastError(`merge "${t.name}"`, e);
  }
}

export async function _tagCtxDelete() {
  const t = _ctxTag();
  if (!t) return;
  const ok = await appConfirm(
    `Delete tag "${t.name}"?`,
    `Removes the tag from ${t.count} photo${t.count === 1 ? "" : "s"}. Photos themselves are untouched.`,
    { okLabel: "Delete", okClass: "danger" },
  );
  if (!ok) return;
  try {
    await apiFetch(`/api/v1/tags/${t.id}`, { method: "DELETE" });
    toast(`Deleted tag "${t.name}"`);
    await loadTagsList();
    showTagsView();
  } catch (e) {
    toastError(`delete "${t.name}"`, e);
  }
}
