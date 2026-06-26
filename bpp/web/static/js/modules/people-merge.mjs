// @ts-check
/**
 * People multi-select, merge, and exclude/include flows.
 *
 * Extracted from people.mjs during the v0.1 cleanup. Owns the
 * `_selectedPeople` set plus the operations users perform on selected
 * clusters: drop-into-target merge, multi-merge into a target picked
 * from a confirm dialog, exclude/include from picks.
 *
 * Re-exported from people.mjs.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { renderAlbumNav } from "./albums.mjs";
import { navigateTo } from "./core.mjs";
import { updateLightboxFaces } from "./lightbox.mjs";
import { scheduleRecompute } from "./analysis.mjs";
import { toast, toastError } from "./toast.mjs";
import { personDisplayName } from "./people.mjs";
import { closeMergePicker } from "./people-pickers.mjs";
import { showPeopleView } from "./people-view.mjs";
import { getPersonAlbumClusterId } from "./people-album-bar.mjs";

export const _selectedPeople = new Set();
let _lastSelectedCid = null;

export function togglePersonSelect(clusterId, e) {
  if (e && e.shiftKey && _lastSelectedCid !== null) {
    const cards = /** @type {HTMLElement[]} */ (
      Array.from(document.querySelectorAll(".person-card[data-cluster-id]"))
    );
    const cids = cards.map(c => Number(c.dataset.clusterId));
    const from = cids.indexOf(_lastSelectedCid);
    const to = cids.indexOf(clusterId);
    if (from >= 0 && to >= 0) {
      const [lo, hi] = from < to ? [from, to] : [to, from];
      for (let i = lo; i <= hi; i++) _selectedPeople.add(cids[i]);
    }
  } else {
    if (_selectedPeople.has(clusterId)) _selectedPeople.delete(clusterId);
    else _selectedPeople.add(clusterId);
  }
  _lastSelectedCid = clusterId;
  _updatePersonSelection();
}

export function clearPersonSelection() {
  _selectedPeople.clear();
  _lastSelectedCid = null;
  _updatePersonSelection();
}

export function _updatePersonSelection() {
  /** @type {NodeListOf<HTMLElement>} */ (
    document.querySelectorAll(".person-card[data-cluster-id]")
  ).forEach(card => {
    const cid = Number(card.dataset.clusterId);
    card.classList.toggle("person-card-selected", _selectedPeople.has(cid));
  });
  const bar = document.getElementById("people-selection-bar");
  if (bar) {
    if (_selectedPeople.size > 0) {
      bar.style.display = "inline-flex";
      bar.querySelector(".selection-count").textContent = `${_selectedPeople.size} selected`;
    } else {
      bar.style.display = "none";
    }
  }
}

export async function mergeSelected(targetCid) {
  if (_selectedPeople.size === 0) return;
  const mergeIds = [..._selectedPeople].filter(cid => cid !== targetCid);
  if (mergeIds.length === 0) return;
  const targetName = personDisplayName(targetCid) || `Person ${targetCid + 1}`;

  const chips = mergeIds.map(cid => {
    const c = state.faceClusters.find(cl => cl.cluster_id === cid);
    if (!c) return "";
    const rep = c.representative;
    const name = personDisplayName(cid) || `Person ${cid + 1}`;
    const src = authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`);
    return `<div class="confirm-face-chip"><img src="${escapeAttr(src)}"><span>${esc(name)}</span></div>`;
  }).join("");
  const targetCluster = state.faceClusters.find(cl => cl.cluster_id === targetCid);
  const targetRep = targetCluster ? targetCluster.representative : null;
  const targetChip = targetRep
    ? `<div class="confirm-face-chip confirm-face-target"><img src="${escapeAttr(authedSrc(`/api/v1/faces/crop/${esc(targetRep.thumb_hash)}/${targetRep.face_index}`))}"><span>${esc(targetName)}</span></div>`
    : "";
  const bodyHTML = `<div class="confirm-face-strip">${chips}<span class="confirm-face-arrow">→</span>${targetChip}</div>`;

  if (!await appConfirm(
    `Merge ${mergeIds.length} people into "${targetName}"?`,
    null,
    {okLabel: "Merge", bodyHTML}
  )) return;
  clearPersonSelection();
  await doMerge(targetCid, mergeIds);
}

export async function excludePerson(clusterId) {
  const cluster = state.faceClusters.find(c => c.cluster_id === clusterId);
  if (!cluster) return;
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  const fps = cluster.filepaths || [];
  if (fps.length === 0) { toast("No photos to exclude", true); return; }
  // Snapshot prior override state so a failed batch can roll back — else the
  // in-memory overrides stay "exclude" while the server never persisted them
  // and the next recompute uses the wrong set.
  const prior = fps.map(fp => [fp, state.overrides[fp]]);
  try {
    const promises = fps.map(fp => {
      state.overrides[fp] = "exclude";
      return apiFetch("/api/v1/override", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({filepath: fp, mode: "exclude"}),
      });
    });
    await Promise.all(promises);
    showPeopleView();
    scheduleRecompute();
    toast(`Excluded all ${fps.length} photos of ${name} from picks`);
  } catch (e) {
    for (const [fp, v] of prior) {
      if (v === undefined) delete state.overrides[fp];
      else state.overrides[fp] = v;
    }
    showPeopleView();
    toastError("exclude this person", e);
  }
}

export async function includePerson(clusterId) {
  const cluster = state.faceClusters.find(c => c.cluster_id === clusterId);
  if (!cluster) return;
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  const fps = (cluster.filepaths || []).filter(fp => state.overrides[fp] === "exclude");
  if (fps.length === 0) { toast("No excluded photos to include", true); return; }
  // These were all "exclude" (filtered above); snapshot so a failed batch
  // can restore them instead of leaving them wrongly cleared in memory.
  const prior = fps.map(fp => [fp, state.overrides[fp]]);
  try {
    const promises = fps.map(fp => {
      delete state.overrides[fp];
      return apiFetch("/api/v1/override", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({filepath: fp, mode: null}),
      });
    });
    await Promise.all(promises);
    showPeopleView();
    scheduleRecompute();
    toast(`Included ${fps.length} photos of ${name} back in picks`);
  } catch (e) {
    for (const [fp, v] of prior) {
      if (v === undefined) delete state.overrides[fp];
      else state.overrides[fp] = v;
    }
    showPeopleView();
    toastError("include this person", e);
  }
}

let _merging = false;
export async function doMerge(primaryId, mergeIds) {
  if (_merging) return;
  _merging = true;
  const primaryName = personDisplayName(primaryId) || `Person ${primaryId + 1}`;
  const mergedNames = mergeIds.map(id => personDisplayName(id) || `Person ${id + 1}`);
  const mergedCount = mergeIds.reduce((sum, id) => {
    const c = state.faceClusters.find(x => x.cluster_id === id);
    return sum + (c ? (c.filepaths || []).length : 0);
  }, 0);
  closeMergePicker();
  /** @type {any} */
  const win = window;
  // In-progress in the status bar (indeterminate); the completion toast
  // below keeps the merged names + photo count, which aren't visible at a
  // glance. (Toast-noise audit item 2.)
  win.showStatusProgress?.("Merging faces…", 0);
  try {
    const resp = await apiFetch("/api/v1/faces/merge", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({primary_cluster_id: primaryId, merge_cluster_ids: mergeIds}),
    });
    if (resp.error) { toast(resp.error, true); return; }
    state.albumList = resp.albums || state.albumList;
    renderAlbumNav();
    clearPersonSelection();
    await loadFaceClusters();
    if (getPersonAlbumClusterId() !== null) {
      navigateTo("people");
    } else {
      showPeopleView();
    }
    if (typeof state.lightboxIdx !== "undefined" && state.lightboxIdx >= 0 && state.lightboxIdx < state.currentGridItems.length) {
      updateLightboxFaces(state.currentGridItems[state.lightboxIdx]);
    }
    const detail = mergedCount ? ` (${mergedCount} photo${mergedCount !== 1 ? "s" : ""})` : "";
    toast(`Merged ${mergedNames.join(", ")} into ${primaryName}${detail}`);
  } catch (e) {
    toastError("merge the people", e);
  } finally {
    win.hideStatusProgress?.();
    _merging = false;
  }
}
