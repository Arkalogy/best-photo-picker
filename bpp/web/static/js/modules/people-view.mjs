// @ts-check
/**
 * People grid view: the filter / sort bar at the top, the selection
 * bar, and the cluster-card grid. Also owns the people view's small
 * helpers (`personLabelHTML`, `isClusterExcluded`,
 * `setPeopleFilter`, `setPeopleSort`).
 *
 * Extracted from people.mjs during the v0.1 cleanup. Re-exported from
 * people.mjs.
 */

import { authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { hide, show } from "./utils.mjs";
import { updateToolbarTitle } from "./core.mjs";
import {
  _selectedPeople,
  personDisplayName,
} from "./people.mjs";
import { setPersonAlbumClusterId } from "./people-album-bar.mjs";
import { expandDismissedSection } from "./people-actions.mjs";
import {
  getAmbiguousPairCount,
  refreshAmbiguousPairCount,
} from "./people-review.mjs";
import { FACE_MIN_PHOTOS } from "./constants.mjs";

export function personLabelHTML(clusterId, photoCount) {
  const name = personDisplayName(clusterId);
  const cluster = state.faceClusters.find(c => c.cluster_id === clusterId);
  const count = photoCount ?? (cluster ? cluster.photo_count : 0);
  const defaultName = `Person ${clusterId + 1}`;
  const displayName = name || defaultName;
  const cls = name ? "person-name" : "person-name unnamed";
  return `<div class="${cls}" data-stop-propagation="true" data-action="startPersonRename" data-arg0="${clusterId}">${esc(displayName)}</div>` +
    `<div class="person-count">${count} photo${count === 1 ? '' : 's'}</div>`;
}

export function isClusterExcluded(c) {
  const fps = c.filepaths || [];
  return fps.length > 0 && fps.every(fp => state.overrides[fp] === "exclude");
}

export function setPeopleFilter(f) {
  state.peopleFilter = f;
  showPeopleView();
}

export function setPeopleSort(s) {
  state.peopleSort = s;
  showPeopleView();
}

/**
 * @param {boolean} [afterLoad] Internal: set when re-rendering after a fetch,
 *   so a genuinely-empty library shows "No Faces Found" instead of looping.
 */
export function showPeopleView(afterLoad) {
  hide("photo-grid");
  hide("person-album-bar");
  hide("person-photo-selection-bar");
  setPersonAlbumClusterId(null);
  state.currentAlbumId = null;
  refreshAmbiguousPairCount();

  const content = document.querySelector(".content");
  let view = document.getElementById("people-view");
  if (!view) {
    view = document.createElement("div");
    view.id = "people-view";
    view.className = "people-grid";
    content.appendChild(view);
  }
  view.classList.remove("hidden");
  show("toolbar");
  show("status-bar");

  if (state.faceClusters.length === 0) {
    // An empty in-memory list might just be stale (tab opened before
    // extraction/clustering finished) — NOT a faceless library. Fetch once
    // before declaring "No Faces Found"; otherwise the view shows a
    // misleading empty state even though the server has thousands of faces.
    if (afterLoad !== true && state.faceRecognitionAvailable) {
      view.innerHTML = `<div class="empty-state people-empty">
        <div class="icon">⏳</div>
        <div class="title">Loading faces…</div>
      </div>`;
      import("./faces.mjs")
        .then((m) => m.loadFaceClusters())
        .then(() => {
          const v = document.getElementById("people-view");
          if (v && !v.classList.contains("hidden")) showPeopleView(true);
        })
        .catch(() => showPeopleView(true));
      return;
    }
    const msg = !state.faceRecognitionAvailable
      ? `Face recognition is not installed.<br><code class="install-hint">pip install bppicker[faces]</code>`
      : "Import and analyze photos to detect faces.";
    view.innerHTML = `<div class="empty-state people-empty">
      <div class="icon">\u{1F464}</div>
      <div class="title">No Faces Found</div>
      <div class="desc">${msg}</div>
    </div>`;
    return;
  }

  const isSignificant = c => (c.photo_count || 0) >= FACE_MIN_PHOTOS || personDisplayName(c.cluster_id);
  const mainIncluded = [], minor = [], excluded = [];
  for (const c of state.faceClusters) {
    if (isClusterExcluded(c)) excluded.push(c);
    else if (isSignificant(c)) mainIncluded.push(c);
    else minor.push(c);
  }

  const sortFn = state.peopleSort === "name"
    ? (a, b) => (personDisplayName(a.cluster_id) || `Person ${a.cluster_id+1}`).localeCompare(personDisplayName(b.cluster_id) || `Person ${b.cluster_id+1}`)
    : (a, b) => b.photo_count - a.photo_count;
  mainIncluded.sort(sortFn);
  minor.sort(sortFn);
  excluded.sort(sortFn);

  let displayCards = [];
  let isMinorView = false;
  if (state.peopleFilter === "minor") { displayCards = minor; isMinorView = true; }
  else if (state.peopleFilter === "excluded") displayCards = excluded;
  else if (state.peopleFilter === "all") displayCards = [...mainIncluded, ...minor, ...excluded];
  else displayCards = mainIncluded;

  const pillTips = {
    included: "People with enough photos to be significant",
    minor: "People with only a few photos — passers-by, background faces",
    excluded: "People you chose to exclude from photo selection",
    ignored: "Faces you dismissed as not real people",
  };
  const pill = (val, label, count) =>
    `<span class="people-filter-pill${state.peopleFilter === val ? ' active' : ''}${count === 0 ? ' disabled' : ''}" title="${escapeAttr(pillTips[val] || '')}" ${count > 0 ? `data-action="setPeopleFilter" data-arg0="${escapeAttr(val)}"` : ''}>${label} (${count})</span>`;
  const sortPill = (val, label) =>
    `<span class="people-filter-pill${state.peopleSort === val ? ' active' : ''}" data-action="setPeopleSort" data-arg0="${val}">${label}</span>`;
  const unnamedIncluded = mainIncluded.filter(c => !personDisplayName(c.cluster_id));
  const showDismiss = state.peopleFilter === "included" && unnamedIncluded.length > 0;
  const dismissBtn = showDismiss
    ? `<button class="people-dismiss-btn" data-action="dismissAllUnnamed">Hide unnamed (${unnamedIncluded.length})</button>`
    : '';
  const showSort = state.peopleFilter !== "ignored";
  const reviewCount = mainIncluded.filter(c => !personDisplayName(c.cluster_id)).length
    + minor.filter(c => !personDisplayName(c.cluster_id)).length;
  const reviewBtn = reviewCount > 0
    ? `<button class="people-review-btn" data-action="startFaceReview">Review (${reviewCount})</button>`
    : '';
  const pairCount = getAmbiguousPairCount();
  const pairLabel = pairCount === null ? "Review pairs (…)" : `Review pairs (${pairCount})`;
  const pairDisabled = pairCount === 0 ? " disabled" : "";
  const reviewPairsBtn = `<button class="people-review-btn" id="btn-review-pairs" data-action="startFacePairReview" title="Find ambiguous cluster pairs and mark same/different to teach the adaptive threshold"${pairDisabled}>${pairLabel}</button>`;
  const filterBar = `<div class="people-filter-bar">
    ${pill("included", "Included", mainIncluded.length)}
    ${pill("minor", "Other", minor.length)}
    ${pill("excluded", "Excluded", excluded.length)}
    ${pill("ignored", "Ignored", state._dismissedCount)}
    <span id="people-selection-bar" class="people-selection-bar" style="display:${_selectedPeople.size > 0 ? 'inline-flex' : 'none'}"
      title="Ctrl+click to select more, then right-click a target to merge">
      <span class="selection-count">${_selectedPeople.size} selected</span>
      <button class="people-selection-clear" data-action="clearPersonSelection" title="Clear selection" aria-label="Clear selection">&times;</button>
    </span>
    ${showSort ? `<span class="people-sort-label">Sort:</span>
    ${sortPill("count", "Photos")}
    ${sortPill("name", "Name")}` : ''}
    ${dismissBtn}
    ${reviewBtn}
    ${reviewPairsBtn}
  </div>`;

  let bodyHTML = "";

  if (state.peopleFilter === "ignored") {
    bodyHTML = `<div class="dismissed-section-body" style="padding:12px 0">
      <div class="dismissed-faces-grid" id="dismissed-faces-grid"></div>
      <div class="dismissed-section-actions">
        <button class="btn btn-secondary btn-sm" data-action="restoreDismissed">Restore all (${state._dismissedCount})</button>
        <button class="btn btn-sm" style="color:var(--red);border-color:var(--red)" data-action="deleteAllDismissed">Delete all permanently</button>
        <span class="dismissed-hint">Restored faces appear as new people. Deleted faces are gone until re-analysis.</span>
      </div>
    </div>`;
  } else {
    const cards = displayCards.map(c => {
      const rep = c.representative;
      const cid = c.cluster_id;
      const exc = isClusterExcluded(c);
      const cardCls = exc ? ' person-card-excluded' : '';
      const cardTitle = exc ? ' title="Excluded from selection"' : '';
      const cls = isSignificant(c) ? "person-card" : "person-card person-card-minor";
      const selCls = _selectedPeople.has(cid) ? " person-card-selected" : "";
      const boostCls = /** @type {any} */ (window).selectedFaceIds?.has(cid) ? " person-card-boosted" : "";
      return `<div class="${cls}${cardCls}${selCls}${boostCls}" data-cluster-id="${cid}"${cardTitle}
        data-action="_personCardClick" data-pass-event="true" data-arg0="${cid}"
        data-oncontextmenu="_personCtxMenuDispatch" data-arg0="${cid}"
        data-onpointerdown="_personPointerDownDispatch" data-arg0="${cid}">
        <div class="person-avatar">
          <img src="${authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`)}" loading="lazy" draggable="false">
        </div>
        <div class="person-label" id="person-label-${cid}">${personLabelHTML(cid, c.photo_count)}</div>
      </div>`;
    }).join("");
    bodyHTML = cards;

    if (displayCards.length === 0) {
      const emptyMsg = state.peopleFilter === "excluded" ? "No excluded people"
        : state.peopleFilter === "minor" ? "No other faces detected" : "No people found";
      bodyHTML = `<div class="empty-state people-empty"><div class="desc">${emptyMsg}</div></div>`;
    }
  }

  view.innerHTML = filterBar + bodyHTML;

  if (state.peopleFilter === "ignored") expandDismissedSection();

  const subtitle = state.peopleFilter === "ignored" ? `${state._dismissedCount} ignored`
    : state.peopleFilter === "excluded" ? `${excluded.length} excluded`
    : state.peopleFilter === "minor" ? `${minor.length} other faces`
    : `${mainIncluded.length} people` + (minor.length > 0 ? ` (+${minor.length} minor)` : "");
  document.getElementById("status-summary").textContent = subtitle;
  updateToolbarTitle("Faces", subtitle);
}
