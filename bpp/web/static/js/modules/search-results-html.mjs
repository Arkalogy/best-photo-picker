// @ts-check
/**
 * The search palette's result list as HTML, one section per result
 * kind. Split out of search.mjs (500-LOC cap). Every rendered row is
 * pushed onto `items` in display order, so `data-arg0` (the index) is
 * what `executeSearchResult` reads back.
 */

import { authedSrc } from "./api-client.mjs";
import { esc } from "./text-format.mjs";

/**
 * @param {number} idx
 * @param {string} iconHTML
 * @param {string} title
 * @param {string | null} subtitle  null: no subtitle line (actions); "" still renders one
 * @param {string} [badge]
 */
function _row(idx, iconHTML, title, subtitle, badge = "") {
  return (
    `<div class="search-result-item" data-idx="${idx}" ` +
    `data-action="executeSearchResult" data-arg0="${idx}">
        <div class="sr-icon">${iconHTML}</div>
        <div class="sr-text">
          <div class="sr-title">${title}</div>
          ${subtitle === null ? "" : `<div class="sr-subtitle">${subtitle}</div>`}
        </div>
        ${badge}
      </div>`
  );
}

/**
 * People, Albums, Dates, Tags: one named row per entry.
 * @param {any[]} entries
 * @param {string} type
 * @param {string} iconHTML
 * @param {(e: any) => string} title
 * @param {(e: any) => string} subtitle
 * @param {any[]} items
 */
function _namedSection(entries, type, iconHTML, title, subtitle, items) {
  let html = "";
  for (const e of entries) {
    const idx = items.length;
    items.push({ type, ...e });
    html += _row(idx, iconHTML, title(e), subtitle(e));
  }
  return html;
}

/**
 * Visual Match and Photos: thumbnail rows with a percentage badge.
 * @param {any[]} photos
 * @param {(p: any) => number} pct
 * @param {string} badgeClass
 * @param {Record<string, string>} ICONS
 * @param {any[]} items
 */
function _photoSection(photos, pct, badgeClass, ICONS, items) {
  let html = "";
  for (const p of photos) {
    const idx = items.length;
    items.push({ type: "photo", ...p });
    const fname = p.filename || p.filepath.split("/").pop();
    const thumbSrc = p.thumb_hash ? authedSrc(`/thumb/${p.thumb_hash}`) : "";
    const imgTag = thumbSrc ? `<img src="${thumbSrc}" alt="" loading="lazy">` : ICONS.library || "";
    html += _row(
      idx,
      imgTag,
      esc(fname),
      esc(p.date_day || ""),
      `<span class="${badgeClass}">${pct(p)}%</span>`
    );
  }
  return html;
}

/**
 * @param {any} data  the /api/v1/search response (plus `_quickActionsMatch`)
 * @param {Record<string, string>} ICONS
 * @param {any[]} items  receives one entry per rendered row, in order
 * @returns {string}
 */
export function searchResultsHTML(data, ICONS, items) {
  let html = "";

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
      const idx = items.length;
      items.push({ type: "action", action: a.action });
      html += _row(idx, actionIcons[a.icon] || ICONS.settings || "", esc(a.label), null);
    }
  }
  if (data.people && data.people.length) {
    html += '<div class="search-category">People</div>';
    html += _namedSection(
      data.people,
      "person",
      ICONS.people || "",
      (p) => esc(p.name),
      (p) => `${p.photo_count} photos`,
      items
    );
  }
  if (data.albums && data.albums.length) {
    html += '<div class="search-category">Albums</div>';
    html += _namedSection(
      data.albums,
      "album",
      ICONS.folder || "",
      (a) => esc(a.name),
      (a) => `${a.photo_count} photos`,
      items
    );
  }
  if (data.dates && data.dates.length) {
    html += '<div class="search-category">Dates</div>';
    html += _namedSection(
      data.dates,
      "date",
      ICONS.calendar || "",
      (d) => esc(d.label),
      () => "Jump to time period",
      items
    );
  }
  if (data.tags && data.tags.length) {
    html += '<div class="search-category">Tags</div>';
    html += _namedSection(
      data.tags,
      "tag",
      ICONS.tag || "",
      (t) => esc(t.name),
      (t) => `${t.photo_count} photos`,
      items
    );
  }
  if (data.semantic && data.semantic.length) {
    html +=
      '<div class="search-category">' +
      '<span class="search-category-ai">AI</span> ' +
      `Visual Match (${data.semantic.length})</div>`;
    html += _photoSection(
      data.semantic.slice(0, 12),
      (p) => Math.round((p.similarity || 0) * 100),
      "sr-badge sr-badge-ai",
      ICONS,
      items
    );
  }
  if (data.photos && data.photos.length) {
    html += '<div class="search-category">' + `Photos (${data.photos.length})</div>`;
    html += _photoSection(
      data.photos.slice(0, 20),
      (p) => Math.round((p.aggregate_score || 0) * 100),
      "sr-badge",
      ICONS,
      items
    );
  }
  return html || _noResultsHTML(data);
}

/** @param {any} data */
function _noResultsHTML(data) {
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
  return (
    '<div class="search-no-results">' +
    "<div>No results found</div>" +
    (hint ? `<div class="search-hint">${hint}</div>` : "") +
    "</div>"
  );
}
