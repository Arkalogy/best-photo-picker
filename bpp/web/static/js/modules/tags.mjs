// @ts-check
/**
 * Tag CRUD UI: lightbox tag chips + grid tag filter + batch-tag
 * dialog + sidebar tag list.
 *
 * Three pieces of state live module-internal:
 *  - allTags: cached `[{id, name, count}]` from /api/tags
 *  - tagFilter: the active grid filter (or null)
 *  - photoTagsCache: photo_id → [{id, name}] used by filterByTag
 *
 * Bridged onto window because albums.js / photos.js / search.js /
 * lightbox.js / app.js all read or call into this surface; the
 * `tagFilter` writer in albums.js uses the new
 * `_setTagFilter()` setter.
 */

import { apiFetch } from "./api-client.mjs";
import { appPrompt } from "./dialogs.mjs";
import { esc, escapeJsAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

/** @typedef {{ id: number, name: string, count?: number }} Tag */
/** @typedef {{ id: number, name: string } | null} TagFilter */

/** @type {Tag[]} */
let allTags = [];
/** @type {TagFilter} */
let tagFilter = null;
/** @type {Record<number, { id: number, name: string }[]>} */
let photoTagsCache = {};

export function _getAllTags() {
  return allTags;
}

/** @param {Tag[]} tags */
export function _setAllTags(tags) {
  allTags = tags;
}

export function _getTagFilter() {
  return tagFilter;
}

/** @param {TagFilter} f */
export function _setTagFilter(f) {
  tagFilter = f;
}

/** @param {Record<number, { id: number, name: string }[]>} cache */
export function _setPhotoTagsCache(cache) {
  photoTagsCache = cache;
}

/**
 * Refresh `allTags` from the server.
 *
 * Protection B: routed through wrapSectionLoader so a /api/v1/tags
 * failure surfaces as a retry pill in the Tags sidebar section
 * instead of silently leaving the list empty (a silent stall, per
 * project convention). The wrapper never throws.
 */
export async function loadAllTags() {
  const { wrapSectionLoader } = await import("./sidebar-safety.mjs");
  return wrapSectionLoader("tags", _loadAllTagsInner);
}

async function _loadAllTagsInner() {
  const data = await apiFetch("/api/v1/tags");
  allTags = data.tags || [];
}

/* ── Lightbox tag chips ── */

/**
 * Render the tag chips for a single photo into `#lb-tags`. Hides
 * the section when the photo has no id (e.g. video, on-this-day
 * synthetic entry). Caches the fetched list keyed by photo_id.
 *
 * @param {{ id?: number }} p
 */
export async function updateLightboxTags(p) {
  const container = document.getElementById("lb-tags");
  if (!container) return;
  if (!p.id) {
    container.classList.add("hidden");
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = '<span class="lb-tags-loading">&hellip;</span>';

  try {
    const data = await apiFetch("/api/v1/photos/" + p.id + "/tags");
    const tags = data.tags || [];
    photoTagsCache[p.id] = tags;
    _renderLightboxTags(p, tags);
  } catch {
    _renderLightboxTags(p, photoTagsCache[p.id] || []);
  }
}

/**
 * Panel-cleanup item 4: no always-open input. Empty state is one quiet
 * labeled button ("+ Add tag" — labeled so users know the section exists);
 * with tags, a proper Tags section with chips + a small "+" to add more.
 * Clicking either swaps in the input (lbShowTagInput).
 *
 * @param {{ id?: number }} p
 * @param {{ id: number, name: string }[]} tags
 * @param {{ showInput?: boolean }} [opts]
 */
export function _renderLightboxTags(p, tags, opts) {
  const container = document.getElementById("lb-tags");
  if (!container) return;
  const showInput = !!(opts && opts.showInput);

  if (!tags.length && !showInput) {
    container.innerHTML =
      `<button class="lb-tag-add-btn" data-action="lbShowTagInput" data-arg0="${p.id}" ` +
      'title="Tag this photo (e.g. birthday, beach)">+ Add tag</button>';
    return;
  }

  let html = '<div class="lb-tags-label">Tags</div>';
  html += '<div class="lb-tags-chips">';
  for (const t of tags) {
    html +=
      '<span class="lb-tag-chip">' +
      esc(t.name) +
      '<button class="lb-tag-remove" data-action="removeLbTag" data-arg0="' +
      p.id +
      '" data-arg1="' +
      t.id +
      '" title="Remove tag">&times;</button>' +
      "</span>";
  }
  if (showInput) {
    html +=
      '<span class="lb-tag-add-wrap">' +
      '<input type="text" class="lb-tag-input" id="lb-tag-input" placeholder="Add tag..." ' +
      'data-oninput="onTagInput" data-onkeydown="onTagKeydown" data-arg0="' +
      p.id +
      '" autocomplete="off">' +
      '<div class="lb-tag-suggest hidden" id="lb-tag-suggest"></div>' +
      "</span>";
  } else {
    html +=
      `<button class="lb-tag-add-btn lb-tag-add-btn--chip" data-action="lbShowTagInput" data-arg0="${p.id}" ` +
      'title="Add another tag">+</button>';
  }
  html += "</div>";
  container.innerHTML = html;
}

/**
 * Swap the Tags section into input mode and focus it. Wired to the
 * "+ Add tag" / "+" buttons via data-action.
 * @param {number} photoId
 */
export function lbShowTagInput(photoId) {
  _renderLightboxTags({ id: photoId }, photoTagsCache[photoId] || [], { showInput: true });
  const input = /** @type {HTMLInputElement | null} */ (document.getElementById("lb-tag-input"));
  if (input) input.focus();
}

/**
 * @param {number} photoId
 * @param {number} tagId
 */
export async function removeLbTag(photoId, tagId) {
  /** @type {any} */
  const win = window;
  try {
    await apiFetch("/api/v1/photos/" + photoId + "/tags/" + tagId, { method: "DELETE" });
    const lbIdx = /** @type {number} */ (win.lightboxIdx);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    const p = items[lbIdx];
    if (p && p.id === photoId) updateLightboxTags(p);
    win.loadAlbumList?.();
  } catch (e) {
    toastError("remove the tag", e);
  }
}

/**
 * @param {number} photoId
 * @param {string} name
 */
export async function addLbTag(photoId, name) {
  /** @type {any} */
  const win = window;
  name = name.trim();
  if (!name) return;
  try {
    await apiFetch("/api/v1/photos/" + photoId + "/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    loadAllTags();
    const lbIdx = /** @type {number} */ (win.lightboxIdx);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    const p = items[lbIdx];
    if (p && p.id === photoId) updateLightboxTags(p);
    win.loadAlbumList?.();
  } catch (e) {
    toastError("add the tag", e);
  }
}

/** @param {HTMLInputElement} input */
export function onTagInput(input) {
  if (input === undefined || typeof input === "string") input = this;
  const q = input.value.trim();
  const suggest = document.getElementById("lb-tag-suggest");
  if (!suggest) return;
  if (q.length === 0) {
    suggest.classList.add("hidden");
    return;
  }

  const matches = allTags
    .filter((t) => t.name.toLowerCase().indexOf(q.toLowerCase()) === 0)
    .slice(0, 8);

  if (matches.length === 0) {
    suggest.classList.add("hidden");
    return;
  }

  suggest.classList.remove("hidden");
  let html = "";
  for (const m of matches) {
    html +=
      '<div class="lb-tag-option" data-onmousedown="pickTagSuggestion" data-arg0="' +
      escapeJsAttr(m.name) +
      '">' +
      esc(m.name) +
      ' <span class="lb-tag-count">(' +
      (m.count || 0) +
      ")</span></div>";
  }
  suggest.innerHTML = html;
}

/**
 * @param {KeyboardEvent} e
 * @param {number} photoId
 */
export function onTagKeydown(e, photoId) {
  if (photoId === undefined) photoId = +this.dataset.arg0;
  e.stopPropagation(); // Prevent lightbox shortcuts (f=favorite, etc.)
  if (e.key === "Enter") {
    e.preventDefault();
    const input = /** @type {HTMLInputElement | null} */ (
      document.getElementById("lb-tag-input")
    );
    if (input && input.value.trim()) {
      addLbTag(photoId, input.value);
    }
  } else if (e.key === "Escape") {
    document.getElementById("lb-tag-suggest")?.classList.add("hidden");
    /** @type {HTMLElement} */ (e.target).blur();
  }
}

/** @param {string} name */
export function pickTagSuggestion(name) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = items[/** @type {number} */ (win.lightboxIdx)];
  if (p && p.id) addLbTag(p.id, name);
}

/* ── Grid tag filter ── */

/**
 * @param {number} tagId
 * @param {string} tagName
 */
export function applyTagFilter(tagId, tagName) {
  tagFilter = { id: tagId, name: tagName };
  /** @type {any} */ (window).renderGrid?.();
}

export function clearTagFilter() {
  tagFilter = null;
  /** @type {any} */ (window).renderGrid?.();
}

/**
 * Filter `items` to only those carrying the active tag. Pass-through
 * when `tagFilter` is null. Uses the photoTagsCache populated by
 * updateLightboxTags + ad-hoc lookups.
 *
 * @template {{ id?: number }} T
 * @param {T[]} items
 * @returns {T[]}
 */
export function filterByTag(items) {
  if (!tagFilter) return items;
  const filterId = tagFilter.id;
  return items.filter((p) => {
    const cached = p.id != null ? photoTagsCache[p.id] : undefined;
    if (!cached) return false;
    return cached.some((t) => t.id === filterId);
  });
}

/** Render the toolbar tag-filter chip — hidden when filter is null. */
export function renderTagFilterChip() {
  const container = document.getElementById("tag-filter-chip");
  if (!container) return;
  if (!tagFilter) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML =
    '<span class="tag-chip-active">' +
    esc(tagFilter.name) +
    ' <span class="tag-chip-clear" data-action="clearTagFilter">&times;</span>' +
    "</span>";
}

/* ── Batch tagging ── */

/**
 * Prompt for a tag name, create it server-side, and apply to every
 * currently multi-selected photo with an id.
 */
export async function batchAddTag() {
  /** @type {any} */
  const win = window;
  const multiSelected = /** @type {Set<string> | undefined} */ (win.multiSelected);
  const items = /** @type {any[]} */ (win.currentGridItems || []);

  const paths = multiSelected ? Array.from(multiSelected) : [];
  if (paths.length === 0) {
    toast("No photos selected", true);
    return;
  }

  /** @type {number[]} */
  const ids = [];
  for (const p of items) {
    if (multiSelected?.has(p.filepath) && p.id) ids.push(p.id);
  }
  if (ids.length === 0) return;

  const name = await appPrompt("Add tag to " + ids.length + " photos", {
    placeholder: "Tag name",
    okLabel: "Add",
  });
  if (!name) return;

  try {
    const tagData = await apiFetch("/api/v1/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await apiFetch("/api/v1/tags/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ photo_ids: ids, tag_id: tagData.id }),
    });
    toast(`Tagged ${ids.length} photos with "${name}"`);
    loadAllTags();
  } catch (e) {
    toastError("tag the photos", e);
  }
}

/* ── Sidebar list ── */

/** Render the sidebar tag list — alphabetical, .active on the filter target. */
export function renderTagsSidebar() {
  const container = document.getElementById("tag-list");
  if (!container) return;
  if (allTags.length === 0) {
    container.innerHTML = '<div class="nav-empty-hint">No tags yet</div>';
    return;
  }
  let html = "";
  const sorted = [...allTags].sort((a, b) => a.name.localeCompare(b.name));
  for (const t of sorted) {
    const active = tagFilter && tagFilter.id === t.id ? " active" : "";
    html +=
      '<div class="nav-item' +
      active +
      '" data-action="applyTagFilter" data-arg0="' +
      t.id +
      '" data-arg1="' +
      escapeJsAttr(t.name) +
      '">' +
      "<span>" +
      esc(t.name) +
      "</span>" +
      '<span class="nav-count">' +
      (t.count || 0) +
      "</span>" +
      "</div>";
  }
  container.innerHTML = html;
}
