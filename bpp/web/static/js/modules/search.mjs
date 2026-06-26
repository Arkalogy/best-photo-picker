// @ts-check
/**
 * Cmd+K universal search overlay. Federated across quick actions, people,
 * albums, dates, tags, semantic CLIP results, and filename matches.
 * Self-attaches global keydown (Cmd+K open/close) and the input's
 * own input/keydown listeners on import.
 *
 * The previous classic implementation used inline
 * `data-action="executeSearchResult" data-arg0="searchResultItems[${idx}]"` strings,
 * which required the result array to be a window global. The module
 * version takes an index instead — `executeSearchResult(idx)` — so the
 * array stays module-private.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { esc } from "./text-format.mjs";

/** @type {ReturnType<typeof setTimeout> | null} */
let searchDebounce = null;
let searchActiveIdx = -1;
/** @type {any[]} */
let searchResultItems = [];
/**
 * Monotonic counter bumped every time a new search query fires. Each
 * in-flight `doSearch` captures the seq at start and only renders if it
 * matches the current value — protects the dropdown from a "leo" response
 * arriving after the user has typed "leon" and a second fetch is pending.
 */
let _searchSeq = 0;
/** @type {AbortController | null} */
let _searchController = null;

/**
 * @returns {Array<{label: string, keywords: string, icon: string, action: () => void}>}
 */
function _quickActions() {
  /** @type {any} */
  const win = window;
  return [
    {
      label: "Settings",
      keywords: "settings preferences config options",
      icon: "gear",
      action: () => win.showSettings?.(),
    },
    {
      label: "Import Photos",
      keywords: "import add photos folder",
      icon: "import",
      action: () => win.showImportModal?.(),
    },
    {
      label: "Export",
      keywords: "export save share download",
      icon: "export",
      action: () => win.showExportModal?.(),
    },
    {
      label: "Search (CLIP)",
      keywords: "search find clip ai visual",
      icon: "search",
      action: () => {
        /* already in search */
      },
    },
    {
      label: "Switch Library",
      keywords: "switch library open change",
      icon: "folder",
      action: () => win.showLibraryPicker?.(),
    },
    {
      label: "Analyze All",
      keywords: "analyze reanalyze scan process",
      icon: "analyze",
      action: () => win.startReanalyze?.(),
    },
  ];
}

/**
 * @param {string} q
 */
function _matchQuickActions(q) {
  const lq = q.toLowerCase();
  return _quickActions().filter(
    (a) => a.label.toLowerCase().includes(lq) || a.keywords.includes(lq)
  );
}

export function showSearch() {
  const overlay = document.getElementById("search-overlay");
  if (!overlay) return;
  overlay.classList.add("visible");
  const input = /** @type {HTMLInputElement | null} */ (document.getElementById("search-input"));
  if (input) {
    input.value = "";
    setTimeout(() => input.focus(), 50);
  }
  const results = document.getElementById("search-results");
  if (results) results.innerHTML = "";
  searchActiveIdx = -1;
  searchResultItems = [];
}

export function hideSearch() {
  const overlay = document.getElementById("search-overlay");
  if (overlay) overlay.classList.remove("visible");
  const input = /** @type {HTMLInputElement | null} */ (document.getElementById("search-input"));
  if (input) input.value = "";
  const results = document.getElementById("search-results");
  if (results) results.innerHTML = "";
  searchActiveIdx = -1;
  searchResultItems = [];
}

/** @returns {boolean} */
export function isSearchOpen() {
  const overlay = document.getElementById("search-overlay");
  return !!overlay && overlay.classList.contains("visible");
}

function updateSearchActive() {
  const items = document.querySelectorAll("#search-results .search-result-item");
  items.forEach((el, i) => {
    el.classList.toggle("active", i === searchActiveIdx);
    if (i === searchActiveIdx) {
      el.scrollIntoView({ block: "nearest" });
    }
  });
}

/**
 * Run a search for the given query and render the dropdown.
 *
 * Sequence-guarded: a fresh keystroke bumps `_searchSeq` and aborts the
 * previous in-flight request. If a late response still slips through
 * (e.g. the abort raced the fulfill microtask), the seq check at the end
 * silently drops the stale write so the dropdown never paints results
 * from a query the user has already moved past.
 *
 * @param {string} q
 */
export async function doSearch(q) {
  _searchSeq += 1;
  const seq = _searchSeq;
  if (_searchController && !_searchController.signal.aborted) {
    _searchController.abort();
  }
  _searchController = new AbortController();
  const signal = _searchController.signal;
  try {
    const data = await apiFetch(`/api/v1/search?q=${encodeURIComponent(q)}`, { signal });
    if (seq !== _searchSeq) return; // a newer keystroke superseded us
    data._quickActionsMatch = _matchQuickActions(q);
    renderSearchResults(data);
  } catch (err) {
    if (err && (err.name === "AbortError" || err.name === "TimeoutError")) return;
    if (seq !== _searchSeq) return;
    const el = document.getElementById("search-results");
    if (el) el.innerHTML = '<div class="search-category">Error searching</div>';
  }
}

/**
 * Test-only reset for the search sequence + controller.
 */
export function _resetSearchSeqForTests() {
  _searchSeq = 0;
  if (_searchController && !_searchController.signal.aborted) {
    _searchController.abort();
  }
  _searchController = null;
}

/**
 * @param {any} data
 */
export function renderSearchResults(data) {
  /** @type {any} */
  const win = window;
  const ICONS = win.ICONS || {};
  const container = document.getElementById("search-results");
  if (!container) return;
  let html = "";
  searchResultItems = [];

  if (data._quickActionsMatch && data._quickActionsMatch.length) {
    html += '<div class="search-category">Actions</div>';
    /** @type {Record<string, string>} */
    const actionIcons = {
      gear: ICONS.settings,
      import: ICONS.importArrow,
      export: ICONS.exportArrow,
      search: ICONS.search,
      folder: ICONS.folder,
      analyze: ICONS.analyze,
    };
    for (const a of data._quickActionsMatch) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "action", action: a.action });
      const icon = actionIcons[a.icon] || ICONS.settings || "";
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${icon}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(a.label)}</div>
        </div>
      </div>`;
    }
  }

  if (data.people && data.people.length) {
    html += '<div class="search-category">People</div>';
    for (const p of data.people) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "person", ...p });
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${ICONS.people || ""}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(p.name)}</div>
          <div class="sr-subtitle">${p.photo_count} photos</div>
        </div>
      </div>`;
    }
  }

  if (data.albums && data.albums.length) {
    html += '<div class="search-category">Albums</div>';
    for (const a of data.albums) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "album", ...a });
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${ICONS.folder || ""}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(a.name)}</div>
          <div class="sr-subtitle">${a.photo_count} photos</div>
        </div>
      </div>`;
    }
  }

  if (data.dates && data.dates.length) {
    html += '<div class="search-category">Dates</div>';
    for (const d of data.dates) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "date", ...d });
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${ICONS.calendar || ""}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(d.label)}</div>
          <div class="sr-subtitle">Jump to time period</div>
        </div>
      </div>`;
    }
  }

  if (data.tags && data.tags.length) {
    html += '<div class="search-category">Tags</div>';
    for (const t of data.tags) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "tag", ...t });
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${ICONS.tag || ""}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(t.name)}</div>
          <div class="sr-subtitle">${t.photo_count} photos</div>
        </div>
      </div>`;
    }
  }

  if (data.semantic && data.semantic.length) {
    html +=
      '<div class="search-category">' +
      '<span class="search-category-ai">AI</span> ' +
      `Visual Match (${data.semantic.length})</div>`;
    for (const p of data.semantic.slice(0, 12)) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "photo", ...p });
      const sim = Math.round((p.similarity || 0) * 100);
      const fname = p.filename || p.filepath.split("/").pop();
      const thumbSrc = p.thumb_hash ? authedSrc(`/thumb/${p.thumb_hash}`) : "";
      const imgTag = thumbSrc
        ? `<img src="${thumbSrc}" alt="" loading="lazy">`
        : ICONS.library || "";
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${imgTag}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(fname)}</div>
          <div class="sr-subtitle">${esc(p.date_day || "")}</div>
        </div>
        <span class="sr-badge sr-badge-ai">${sim}%</span>
      </div>`;
    }
  }

  if (data.photos && data.photos.length) {
    html += '<div class="search-category">' + `Photos (${data.photos.length})</div>`;
    for (const p of data.photos.slice(0, 20)) {
      const idx = searchResultItems.length;
      searchResultItems.push({ type: "photo", ...p });
      const score = Math.round((p.aggregate_score || 0) * 100);
      const fname = p.filename || p.filepath.split("/").pop();
      const thumbSrc = p.thumb_hash ? authedSrc(`/thumb/${p.thumb_hash}`) : "";
      const imgTag = thumbSrc
        ? `<img src="${thumbSrc}" alt="" loading="lazy">`
        : ICONS.library || "";
      html +=
        `<div class="search-result-item" data-idx="${idx}" ` +
        `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${imgTag}</div>
        <div class="sr-text">
          <div class="sr-title">${esc(fname)}</div>
          <div class="sr-subtitle">${esc(p.date_day || "")}</div>
        </div>
        <span class="sr-badge">${score}%</span>
      </div>`;
    }
  }

  if (!html) {
    let hint = "";
    const cs = data.clip_status;
    if (cs && !cs.ready) {
      if (!cs.models_available) {
        hint =
          "Visual search needs CLIP models. Run <strong>Analyze All</strong> to download and enable.";
      } else if (cs.embedding_count === 0) {
        hint =
          "Visual search is available but no photos have been indexed yet. Run <strong>Analyze All</strong> to enable visual search.";
      }
    }
    html =
      '<div class="search-no-results">' +
      "<div>No results found</div>" +
      (hint ? `<div class="search-hint">${hint}</div>` : "") +
      "</div>";
  }
  container.innerHTML = html;
  searchActiveIdx = searchResultItems.length > 0 ? 0 : -1;
  updateSearchActive();
}

/**
 * @param {number} idx
 */
export function executeSearchResult(idx) {
  /** @type {any} */
  const win = window;
  const item = searchResultItems[idx];
  if (!item) return;
  hideSearch();
  if (item.type === "action") {
    item.action();
    return;
  }
  if (item.type === "tag") {
    const albums = /** @type {any[]} */ (win.albumList || []);
    const allAlbum = albums.find((a) => a.album_type === "all");
    if (allAlbum) win.switchAlbum?.(allAlbum.id);
    win.applyTagFilter?.(item.id, item.name);
    return;
  }
  if (item.type === "person" || item.type === "album") {
    const albumId = item.album_id || item.id;
    win.switchAlbum?.(albumId);
  } else if (item.type === "date") {
    const albums = /** @type {any[]} */ (win.albumList || []);
    const timeAlbum = albums.find((a) => {
      if (a.album_type !== "smart_time") return false;
      const rule = typeof a.rule === "string" ? JSON.parse(a.rule) : a.rule;
      if (!rule) return false;
      if (item.year && !item.month && rule.year === String(item.year)) return true;
      if (item.date_month && rule.year === String(item.year)) return true;
      return false;
    });
    if (timeAlbum) {
      win.switchAlbum?.(timeAlbum.id);
    } else {
      const allAlbum = albums.find((a) => a.album_type === "all");
      if (allAlbum) win.switchAlbum?.(allAlbum.id);
    }
  } else if (item.type === "photo") {
    scrollToPhotoAndOpen(item.filepath);
  }
}

/**
 * @param {string} filepath
 */
export async function scrollToPhotoAndOpen(filepath) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const gridIdx = items.findIndex((p) => p.filepath === filepath);
  if (gridIdx >= 0) {
    win.openLightbox?.(gridIdx);
    return;
  }
  const albums = /** @type {any[]} */ (win.albumList || []);
  const allAlbum = albums.find((a) => a.album_type === "all");
  if (allAlbum && win.switchAlbum) {
    await win.switchAlbum(allAlbum.id);
    const items2 = /** @type {any[]} */ (win.currentGridItems || []);
    const idx2 = items2.findIndex((p) => p.filepath === filepath);
    if (idx2 >= 0) win.openLightbox?.(idx2);
  }
}

/** Test-only: read internal result array length. */
export function _getSearchResultCount() {
  return searchResultItems.length;
}

/** Test-only: read active result index. */
export function _getSearchActiveIdx() {
  return searchActiveIdx;
}

/** Test-only: reset internal state. */
export function _resetSearchState() {
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = null;
  searchActiveIdx = -1;
  searchResultItems = [];
}

document.addEventListener(
  "keydown",
  (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.code === "KeyK")) {
      e.preventDefault();
      e.stopPropagation();
      if (isSearchOpen()) hideSearch();
      else showSearch();
    }
  },
  true
);

// Wire input + keydown listeners. Index.html includes #search-input
// in the static template, but when the module is imported in the test
// harness those nodes don't exist — guard each lookup.
const _searchInput = /** @type {HTMLInputElement | null} */ (
  document.getElementById("search-input")
);
if (_searchInput) {
  _searchInput.addEventListener("input", (e) => {
    if (searchDebounce) clearTimeout(searchDebounce);
    const q = /** @type {HTMLInputElement} */ (e.target).value.trim();
    if (!q) {
      const results = document.getElementById("search-results");
      if (results) results.innerHTML = "";
      searchActiveIdx = -1;
      searchResultItems = [];
      return;
    }
    searchDebounce = setTimeout(() => doSearch(q), 200);
  });

  _searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      hideSearch();
      e.preventDefault();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      searchActiveIdx = Math.min(searchActiveIdx + 1, searchResultItems.length - 1);
      updateSearchActive();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      searchActiveIdx = Math.max(searchActiveIdx - 1, 0);
      updateSearchActive();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (searchActiveIdx >= 0 && searchActiveIdx < searchResultItems.length) {
        executeSearchResult(searchActiveIdx);
      }
      return;
    }
  });
}
