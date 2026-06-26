// @ts-check
/**
 * Person-album action bar — the toolbar that appears when the user
 * navigates into a smart_person album.
 *
 * Extracted from people.mjs during the v0.1 cleanup. This module
 * owns the bar's render + every _paXxx handler it dispatches:
 *
 *   * updatePersonAlbumBar       — paint or hide based on album
 *   * updatePersonPhotoSelection — toggle the multi-select tray
 *   * Rename / merge / split / avatar / exclude / not-a-face / dismiss
 *     handlers (_paRename, _paMergeWith, _paToggleExclude,
 *     _paNotAFace, _paDismiss)
 *   * Multi-select handlers (_paNotAFaceSelected, _paNotThisPerson,
 *     _paMoveTo, _paNewPerson)
 *   * _getSelectedFaceIds        — helper used by the multi-select
 *                                  handlers to resolve filepaths to
 *                                  face_ids
 *
 * `_personAlbumClusterId` is the module-local that remembers which
 * cluster the bar is currently showing, so handlers know what to act
 * on without re-passing the cluster_id every call.
 *
 * Re-exported from people.mjs so the modules-bridge in
 * templates/index.html keeps exposing every data-action handler on
 * window.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appConfirm, appPrompt } from "./dialogs.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { CLUSTER_DISMISSED } from "./constants.mjs";
import { loadAlbumList, switchAlbum } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { clearMultiSelect } from "./photos.mjs";
import { toast, toastError } from "./toast.mjs";
import { navigateTo, updateToolbarTitle } from "./core.mjs";
import {
  excludePerson,
  includePerson,
  isClusterExcluded,
  personDisplayName,
  showAvatarPicker,
  showMergePicker,
  showSplitPicker,
} from "./people.mjs";

let _personAlbumClusterId = null;

export function getPersonAlbumClusterId() {
  return _personAlbumClusterId;
}

export function setPersonAlbumClusterId(cid) {
  _personAlbumClusterId = cid;
}


export function updatePersonAlbumBar(album) {
  const bar = document.getElementById("person-album-bar");
  const selBar = document.getElementById("person-photo-selection-bar");
  if (!bar) return;

  // Hide bars when not viewing a person album
  if (!album || album.album_type !== "smart_person") {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    if (selBar) { selBar.classList.add("hidden"); selBar.innerHTML = ""; }
    _personAlbumClusterId = null;
    return;
  }

  const cid = album.rule ? album.rule.cluster_id : null;
  _personAlbumClusterId = cid;
  const name = album.name || `Person ${(cid || 0) + 1}`;

  bar.classList.remove("hidden");
  const excl = state.faceClusters.find(c => c.cluster_id === cid);
  const isExcluded = excl && excl.excluded;
  const exclLabel = isExcluded ? "Include in picks" : "Exclude from picks";
  const exclTitle = isExcluded ? "Re-include this person's photos in picks" : "Keep in face lists but never pick their photos";
  bar.innerHTML =
    `<span class="people-filter-pill" data-action="_paRename" data-arg0="${album.id}" data-arg1="${escapeJsAttr(name)}" title="Give this person a name">Rename</span>` +
    `<span class="people-filter-pill" data-action="showAvatarPicker" data-arg0="${cid}" title="Choose a different face photo as avatar">Change avatar</span>` +
    `<span class="people-filter-pill" data-action="_paMergeWith" data-arg0="${cid}" title="Combine this person with another">Merge with\u2026</span>` +
    `<span class="people-filter-pill" data-action="showSplitPicker" data-arg0="${cid}" title="Select wrong faces and move them to a new person">Split\u2026</span>` +
    `<span class="people-filter-pill" data-action="_paToggleExclude" data-arg0="${cid}" title="${escapeAttr(exclTitle)}">${exclLabel}</span>` +
    `<span class="people-filter-pill" style="color:var(--red)" data-action="_paNotAFace" data-arg0="${cid}" title="Mark as false detection — these aren't real faces">Not a face</span>` +
    `<span class="people-filter-pill" style="color:var(--red)" data-action="_paDismiss" data-arg0="${cid}" title="Remove from sidebar, chips, and scoring. Recoverable from Ignored.">Dismiss</span>` +
    `<span class="pa-spacer"></span>` +
    `<span style="font-size:11px;color:var(--text3)">Cmd+click photos to reassign</span>`;

  // Reset photo selection bar
  if (selBar) { selBar.classList.add("hidden"); selBar.innerHTML = ""; }
}

export async function _paRename(albumId, currentName) {
  const name = await appPrompt("Name this person", {placeholder: "Name", value: currentName, okLabel: "Save"});
  if (!name || !name.trim() || name.trim() === currentName) return;
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: name.trim()}),
    });
    await loadAlbumList();
    const album = state.albumList.find(a => a.id === albumId);
    if (album) {
      updateToolbarTitle(album.name);
      updatePersonAlbumBar(album);
    }
    // No toast: updateToolbarTitle + updatePersonAlbumBar update the name in
    // place, so the rename is visible. (Toast-noise audit item 14.)
  } catch (e) {
    toastError("rename the person", e);
  }
}

export function _paMergeWith(clusterId) {
  showMergePicker(clusterId);
}

export async function _paToggleExclude(clusterId) {
  // Match the context-menu flow in line ~425: route through the split
  // include/exclude helpers based on current state.
  const cluster = state.faceClusters.find(c => c.cluster_id === clusterId);
  if (cluster && isClusterExcluded(cluster)) {
    await includePerson(clusterId);
  } else {
    await excludePerson(clusterId);
  }
  // Refresh the bar to update the label
  const album = state.albumList.find(a =>
    a.album_type === "smart_person" && a.rule && a.rule.cluster_id === clusterId
  );
  if (album) updatePersonAlbumBar(album);
}

export async function _paNotAFace(clusterId) {
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  if (!await appConfirm(
    `Mark "${name}" as not a face?`,
    "All detections in this group are false positives. Moved to Ignored.",
    {okLabel: "Not a face", okClass: "danger"},
  )) return;
  try {
    await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cluster_ids: [clusterId]}),
    });
    toast(`Marked as not a face — moved to Ignored`);
    await loadAlbumList();
    await loadFaceClusters();
    navigateTo("people");
  } catch (e) {
    toastError("mark as not a face", e);
  }
}

export async function _paDismiss(clusterId) {
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  if (!await appConfirm(`Dismiss "${name}"?`, "This removes them from the people list. You can restore later from Ignored.")) return;
  try {
    await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cluster_ids: [clusterId]}),
    });
    toast(`Dismissed "${name}" — restore anytime from Ignored`);
    await loadAlbumList();
    await loadFaceClusters();
    navigateTo("people");
  } catch (e) {
    toastError("dismiss the person", e);
  }
}

// ── Person Album Photo Selection ──
// When user selects photos in a person album grid, show reassign actions.

export function updatePersonPhotoSelection(selectedCount, selectedFilepaths) {
  const selBar = document.getElementById("person-photo-selection-bar");
  if (!selBar || !_personAlbumClusterId) return;

  if (selectedCount === 0) {
    selBar.classList.add("hidden");
    selBar.innerHTML = "";
    return;
  }

  const album = state.albumList.find(a =>
    a.album_type === "smart_person" && a.rule && a.rule.cluster_id === _personAlbumClusterId
  );
  const pName = album ? album.name : "this person";
  selBar.classList.remove("hidden");
  selBar.innerHTML =
    `<span class="selection-count">${selectedCount} selected</span>` +
    `<span class="people-filter-pill" data-action="_paNotThisPerson">Remove from ${esc(pName)}</span>` +
    `<span class="people-filter-pill" data-action="_paMoveTo">Reassign to\u2026</span>` +
    `<span class="people-filter-pill" style="color:var(--red)" data-action="_paNotAFaceSelected">Not a face</span>` +
    `<span class="people-filter-pill" data-action="clearMultiSelect">Clear</span>`;
}

export async function _paNotAFaceSelected() {
  const cid = _personAlbumClusterId;
  if (!cid && cid !== 0) return;
  const selected = await _getSelectedFaceIds(cid);
  // Silent guard: the multi-select bar is hidden when nothing is selected
  // (see updatePersonPhotoSelection), so these handlers are unreachable with
  // an empty selection — the disabled affordance is the hidden bar, no toast.
  if (!selected.length) return;
  const n = selected.length;
  if (!await appConfirm(
    `Mark ${n} selected photo${n !== 1 ? "s" : ""} as not a face?`,
    "These detections are false positives and will be dismissed.",
    {okLabel: "Not a face", okClass: "danger"},
  )) return;
  try {
    // Reassign each face to CLUSTER_DISMISSED
    await Promise.all(selected.map(fid =>
      apiFetch("/api/v1/faces/reassign", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({face_id: fid, cluster_id: CLUSTER_DISMISSED}),
      })
    ));
    toast(`Marked ${n} detection${n !== 1 ? "s" : ""} as not a face`);
    clearMultiSelect();
    await loadAlbumList();
    await loadFaceClusters();
    if (state.currentAlbumId) switchAlbum(state.currentAlbumId, {force: true});
  } catch (e) {
    toastError("mark the detections as not a face", e);
  }
}

export async function _paNotThisPerson() {
  const cid = _personAlbumClusterId;
  if (!cid && cid !== 0) return;
  const selected = await _getSelectedFaceIds(cid);
  // Silent guard: the multi-select bar is hidden when nothing is selected
  // (see updatePersonPhotoSelection), so these handlers are unreachable with
  // an empty selection — the disabled affordance is the hidden bar, no toast.
  if (!selected.length) return;
  try {
    const resp = await apiFetch("/api/v1/faces/split", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({face_ids: selected}),
    });
    const pName = personDisplayName(cid) || `Person ${cid + 1}`;
    toast(`Removed ${selected.length} photo${selected.length !== 1 ? "s" : ""} from ${pName} and created a new person`);
    clearMultiSelect();
    await loadAlbumList();
    await loadFaceClusters();
    // Reload current album
    if (state.currentAlbumId) switchAlbum(state.currentAlbumId, {force: true});
  } catch (e) {
    toastError("reassign the faces", e);
  }
}

export async function _paMoveTo() {
  const cid = _personAlbumClusterId;
  if (!cid && cid !== 0) return;
  const selected = await _getSelectedFaceIds(cid);
  // Silent guard: the multi-select bar is hidden when nothing is selected
  // (see updatePersonPhotoSelection), so these handlers are unreachable with
  // an empty selection — the disabled affordance is the hidden bar, no toast.
  if (!selected.length) return;

  // Show merge picker to choose target person
  const existing = document.getElementById("merge-picker-overlay");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "merge-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  header.textContent = `Reassign ${selected.length} photo${selected.length !== 1 ? "s" : ""} to\u2026`;
  picker.appendChild(header);

  const grid = document.createElement("div");
  grid.className = "merge-picker-grid";

  // "New person" card at top
  const newCard = document.createElement("div");
  newCard.className = "merge-picker-card";
  newCard.innerHTML = `<span style="font-size:24px;line-height:48px">+</span><span>New person</span>`;
  newCard.onclick = async () => {
    overlay.remove();
    const name = await appPrompt("Name for the new person", {placeholder: "Name (optional)", okLabel: "Create"});
    if (name === null) return;
    try {
      const resp = await apiFetch("/api/v1/faces/split", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({face_ids: selected}),
      });
      if (name && name.trim() && resp.new_cluster_id != null) {
        await loadAlbumList();
        await loadFaceClusters();
        const newAlbum = state.albumList.find(a =>
          a.album_type === "smart_person" && a.rule && a.rule.cluster_id === resp.new_cluster_id
        );
        if (newAlbum) {
          await apiFetch(`/api/v1/albums/${newAlbum.id}`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: name.trim()}),
          });
        }
      }
      const srcName = personDisplayName(cid) || `Person ${cid + 1}`;
      const destName = name && name.trim() ? `"${name.trim()}"` : "a new person";
      toast(`Moved ${selected.length} photo${selected.length !== 1 ? "s" : ""} from ${srcName} to ${destName}`);
      clearMultiSelect();
      await loadAlbumList();
      await loadFaceClusters();
      if (state.currentAlbumId) switchAlbum(state.currentAlbumId, {force: true});
    } catch (e) {
      toastError("move the photos", e);
    }
  };
  grid.appendChild(newCard);

  for (const c of state.faceClusters) {
    if (c.cluster_id === cid) continue;
    const card = document.createElement("div");
    card.className = "merge-picker-card";
    const name = personDisplayName(c.cluster_id) || `Person ${c.cluster_id + 1}`;
    const rep = c.representative;
    const src = rep ? authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`) : "";
    card.innerHTML = `<img src="${escapeAttr(src)}" loading="lazy"><span>${esc(name)}</span>`;
    card.onclick = async () => {
      overlay.remove();
      try {
        // Split out of current cluster, then merge into target
        const splitResp = await apiFetch("/api/v1/faces/split", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({face_ids: selected}),
        });
        if (splitResp.new_cluster_id != null) {
          await apiFetch("/api/v1/faces/merge", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              primary_cluster_id: c.cluster_id,
              merge_cluster_ids: [splitResp.new_cluster_id],
            }),
          });
        }
        const srcName = personDisplayName(cid) || `Person ${cid + 1}`;
        toast(`Moved ${selected.length} photo${selected.length !== 1 ? "s" : ""} from ${srcName} to ${name}`);
        clearMultiSelect();
        await loadAlbumList();
        await loadFaceClusters();
        if (state.currentAlbumId) switchAlbum(state.currentAlbumId, {force: true});
      } catch (e) {
        toastError("move the faces", e);
      }
    };
    grid.appendChild(card);
  }
  picker.appendChild(grid);
  overlay.appendChild(picker);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}

export async function _paNewPerson() {
  const cid = _personAlbumClusterId;
  if (!cid && cid !== 0) return;
  const selected = await _getSelectedFaceIds(cid);
  // Silent guard: the multi-select bar is hidden when nothing is selected
  // (see updatePersonPhotoSelection), so these handlers are unreachable with
  // an empty selection — the disabled affordance is the hidden bar, no toast.
  if (!selected.length) return;

  const name = await appPrompt("Name for the new person", {placeholder: "Name (optional)", okLabel: "Create"});
  if (name === null) return; // cancelled
  try {
    const resp = await apiFetch("/api/v1/faces/split", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({face_ids: selected}),
    });
    if (name && name.trim() && resp.new_cluster_id != null) {
      // Find or create album for the new cluster, then rename
      await loadAlbumList();
      await loadFaceClusters();
      const newAlbum = state.albumList.find(a =>
        a.album_type === "smart_person" && a.rule && a.rule.cluster_id === resp.new_cluster_id
      );
      if (newAlbum) {
        await apiFetch(`/api/v1/albums/${newAlbum.id}`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({name: name.trim()}),
        });
      }
    }
    const srcName = personDisplayName(cid) || `Person ${cid + 1}`;
    const destName = name && name.trim() ? `"${name.trim()}"` : "a new person";
    toast(`Moved ${selected.length} photo${selected.length !== 1 ? "s" : ""} from ${srcName} to ${destName}`);
    clearMultiSelect();
    await loadAlbumList();
    await loadFaceClusters();
    if (state.currentAlbumId) switchAlbum(state.currentAlbumId, {force: true});
  } catch (e) {
    toastError("split the group", e);
  }
}

export async function _getSelectedFaceIds(clusterId) {
  // Fetch face data for the cluster and match against selected photos
  if (typeof state.multiSelected === "undefined" || !state.multiSelected.size) return [];
  try {
    const data = await apiFetch(`/api/v1/faces/cluster/${clusterId}?limit=9999`);
    const faces = data.faces || [];
    const selectedPaths = new Set(state.multiSelected);
    const faceIds = [];
    for (const f of faces) {
      if (selectedPaths.has(f.filepath)) {
        faceIds.push(f.face_id);
      }
    }
    return faceIds;
  } catch (e) {
    toastError("load face data", e);
    return [];
  }
}
