// @ts-check
/**
 * The photo grid's pure steps: which photos the filter keeps, how the
 * sort orders them, the empty-state copy, and the similar-photo cluster
 * map the lightbox strip reads. Split out of photos.mjs (500-LOC cap);
 * `_doRenderGrid` there calls these in order.
 */

import { esc } from "./text-format.mjs";

/**
 * @param {any[]} photos
 * @param {string} filterBy
 * @param {{selectedPaths: Set<string>, favorites: Set<string>, overrides: Record<string, string>}} ctx
 * @returns {any[]}
 */
export function filterGridItems(photos, filterBy, { selectedPaths, favorites, overrides }) {
  let items = [...photos];
  if (filterBy === "deleted") {
    items = items.filter((p) => p.deleted_at);
  } else {
    items = items.filter((p) => !p.deleted_at);
    if (filterBy === "selected") items = items.filter((p) => selectedPaths.has(p.filepath));
    else if (filterBy === "not-selected")
      items = items.filter((p) => !selectedPaths.has(p.filepath));
    else if (filterBy === "favorites") items = items.filter((p) => favorites.has(p.filepath));
    else if (filterBy === "overridden") items = items.filter((p) => overrides[p.filepath]);
    else if (filterBy === "enhanced") items = items.filter((p) => p._enhanced || p._auto_enhanced);
    else if (filterBy === "photos-only") items = items.filter((p) => !p.is_video && !p.is_raw);
    else if (filterBy === "videos-only") items = items.filter((p) => p.is_video);
    else if (filterBy === "raw-only") items = items.filter((p) => p.is_raw);
  }
  return items;
}

/**
 * Sorts in place.
 * @param {any[]} items
 * @param {string} sortBy
 */
export function sortGridItems(items, sortBy) {
  if (sortBy === "score-desc")
    items.sort((a, b) => (b.aggregate_score || 0) - (a.aggregate_score || 0));
  else if (sortBy === "score-asc")
    items.sort((a, b) => (a.aggregate_score || 0) - (b.aggregate_score || 0));
  else if (sortBy === "date-asc") items.sort((a, b) => (a.date || "").localeCompare(b.date || ""));
  else if (sortBy === "date-desc") items.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  else if (sortBy === "name")
    items.sort((a, b) => (a.filename || "").localeCompare(b.filename || ""));
  else if (sortBy === "size-desc") items.sort((a, b) => (b.file_size || 0) - (a.file_size || 0));
  else if (sortBy === "size-asc") items.sort((a, b) => (a.file_size || 0) - (b.file_size || 0));
  else if (sortBy === "faces-desc") items.sort((a, b) => (b.face_count || 0) - (a.face_count || 0));
}

// Each empty-state carries a one-line hint explaining the
// action that populates the filter. Without the hints a first-time
// user clicking 'Favorites' on a brand-new library saw a bare
// 'No favorited photos' and concluded the feature was broken
// rather than 'I haven't used this feature yet'.
/** @type {Record<string, {label: string, hint?: string}>} */
const EMPTY_STATES = {
  selected: {
    label: "selected",
    hint: "Selection is what BPP Picks chose — recompute picks or adjust the K slider.",
  },
  "not-selected": {
    label: "not selected",
    hint: "Every photo here is in your current picks.",
  },
  favorites: {
    label: "favorited",
    hint: "Click the heart icon on any photo to add it here.",
  },
  overridden: {
    label: "overridden",
    hint: "Right-click any photo and choose Include or Exclude to add an override.",
  },
  enhanced: {
    label: "enhanced",
    hint: "Open a photo in the lightbox and use the Edit pane to enhance it.",
  },
  deleted: {
    label: "deleted",
    hint: "Deleted photos appear here for 30 days before they're permanently removed.",
  },
  "photos-only": { label: "photo", hint: "Try All to see videos and RAWs too." },
  "videos-only": { label: "video", hint: "Import a folder containing .mp4 / .mov files." },
  "raw-only": { label: "RAW", hint: "Import a folder containing camera RAW files." },
};

/**
 * @param {string} filterBy
 * @returns {string}
 */
export function emptyStateHTML(filterBy) {
  const entry = EMPTY_STATES[filterBy] || { label: filterBy };
  return (
    `<div class="grid-empty-msg">No ${esc(entry.label)} photos` +
    (entry.hint ? `<div class="grid-empty-hint">${esc(entry.hint)}</div>` : "") +
    `</div>`
  );
}

/**
 * filepath -> the filepaths of its near-duplicate cluster, for the
 * lightbox similar-photos strip.
 * @param {any[]} items
 * @returns {Record<string, string[]>}
 */
export function buildSimClusterMap(items) {
  /** @type {Record<string, string[]>} */
  const sim = {};
  // Build from explicit similar_photos (Duplicates album API response)
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    if (it && it.similar_photos && it.similar_photos.length > 0) {
      const cluster = [it.filepath];
      for (let j = 0; j < it.similar_photos.length; j++) {
        cluster.push(it.similar_photos[j].filepath);
      }
      for (let k = 0; k < cluster.length; k++) {
        sim[cluster[k]] = cluster;
      }
    }
  }
  // Also build from dup_cluster_id so the lightbox similar-photos strip
  // works from any view (Library, Needs Review, etc.), not just Duplicates.
  /** @type {Record<number, string[]>} */
  const byCluster = {};
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const cid = it && it.dup_cluster_id;
    if (cid && cid > 0) {
      if (!byCluster[cid]) byCluster[cid] = [];
      byCluster[cid].push(it.filepath);
    }
  }
  for (const paths of Object.values(byCluster)) {
    if (paths.length > 1) {
      for (let k = 0; k < paths.length; k++) {
        if (!sim[paths[k]]) sim[paths[k]] = paths;
      }
    }
  }
  return sim;
}
