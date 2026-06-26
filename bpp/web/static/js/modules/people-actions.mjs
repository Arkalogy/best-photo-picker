// @ts-check
/**
 * Person + face mutation actions: not-a-face, dismiss, restore, delete,
 * dismissed-section loaders.
 *
 * Extracted from people.mjs during the v0.1 cleanup. Owns the
 * single-cluster lifecycle handlers that don't need a picker:
 *
 *   * notAFaceCluster, dismissPerson, dismissAllUnnamed
 *   * restoreDismissed, restoreFace
 *   * deleteFacePermanently, deleteAllDismissed
 *   * loadDismissedFaces, expandDismissedSection
 *
 * Re-exported from people.mjs so the modules-bridge keeps exposing
 * every data-action handler on window unchanged.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { loadAlbumList, renderAlbumNav } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { toast, toastError } from "./toast.mjs";
import { isClusterExcluded, personDisplayName, showPeopleView } from "./people.mjs";


export async function notAFaceCluster(clusterId) {
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  const cluster = state.faceClusters.find(c => c.cluster_id === clusterId);
  const n = cluster ? (cluster.filepaths || []).length : 0;
  const detail = n ? ` (${n} detection${n !== 1 ? "s" : ""})` : "";
  if (!await appConfirm(
    `Mark "${name}" as not a face?${detail}`,
    "All detections in this group are false positives. They will be moved to Ignored and can be permanently deleted from there.",
    {okLabel: "Not a face", okClass: "danger"},
  )) return;
  try {
    const resp = await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cluster_id: clusterId}),
    });
    if (resp.albums) { state.albumList = resp.albums; renderAlbumNav(); }
    await loadFaceClusters();
    showPeopleView();
    toast(`Marked ${n} detection${n !== 1 ? "s" : ""} as not a face — moved to Ignored`);
  } catch (e) {
    toastError("mark this as not a face", e);
  }
}

// ── Faces: dismiss ──
export async function dismissPerson(clusterId) {
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  if (!await appConfirm(`Dismiss "${name}"? Removes from sidebar, chips, and scoring. Restore later from Ignored faces section.`, {okLabel: "Dismiss", okClass: "danger"})) return;
  const photoCount = (state.faceClusters.find(c => c.cluster_id === clusterId) || {}).filepaths?.length || 0;
  try {
    const resp = await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cluster_id: clusterId}),
    });
    if (resp.albums) { state.albumList = resp.albums; renderAlbumNav(); }
    await loadFaceClusters();
    showPeopleView();
    const countDetail = photoCount ? ` and their ${photoCount} photo${photoCount !== 1 ? "s" : ""}` : "";
    toast(`Dismissed "${name}"${countDetail} — restore anytime from Ignored`);
  } catch (e) {
    toastError(`dismiss "${name}"`, e);
  }
}

export async function dismissAllUnnamed() {
  // Only dismiss unnamed faces that are currently included (not already excluded)
  const included = state.faceClusters.filter(c => !isClusterExcluded(c));
  const unnamed = included.filter(c => !personDisplayName(c.cluster_id));
  if (unnamed.length === 0) { toast("No unnamed faces to hide"); return; }
  const n = unnamed.length;
  if (!await appConfirm(
    `Hide ${n} unnamed face${n === 1 ? '' : 's'}?`,
    "They will be removed from the People view. Named people are kept. You can re-detect faces to bring them back.",
    {okLabel: `Hide ${n}`, okClass: "danger"}
  )) return;
  const totalPhotos = unnamed.reduce((sum, c) => sum + (c.filepaths || []).length, 0);
  const ids = unnamed.map(c => c.cluster_id);
  try {
    const resp = await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cluster_ids: ids}),
    });
    if (resp.albums) { state.albumList = resp.albums; renderAlbumNav(); }
    await loadFaceClusters();
    showPeopleView();
    toast(`Hidden ${n} unnamed person${n === 1 ? '' : 's'} (${totalPhotos} photos) — restore from Ignored`);
  } catch (e) {
    toastError("hide the unnamed faces", e);
  }
}

// ── Faces: restore dismissed ──

export async function restoreDismissed() {
  if (!await appConfirm(`Restore all ${state._dismissedCount} ignored face${state._dismissedCount === 1 ? '' : 's'}?`, {okLabel: "Restore", okClass: "primary"})) return;
  try {
    const resp = await apiFetch("/api/v1/faces/restore", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({all: true}),
    });
    if (resp.albums) { state.albumList = resp.albums; renderAlbumNav(); }
    state._dismissedCount = 0;
    state._dismissedFaces = null;
    state.peopleFilter = "included"; // switch away from empty Ignored tab
    await loadFaceClusters();
    showPeopleView();
    toast(`Restored ${resp.count} person${resp.count === 1 ? '' : 's'} back to the People view`);
  } catch (e) {
    toastError("restore the ignored faces", e);
  }
}

export async function restoreFace(faceId) {
  // Optimistic DOM update — remove face cell and update counts immediately.
  // The cell renders the button with data-action/data-arg0 (NOT inline
  // onclick), so match on that; the old onclick selector never matched and
  // the restored face lingered in the Ignored grid.
  const btn = document.querySelector(`.dismissed-face-restore[data-arg0="${faceId}"]`);
  if (btn) btn.closest(".dismissed-face-cell")?.remove();
  if (state._dismissedFaces) {
    state._dismissedFaces = state._dismissedFaces.filter(f => f.face_id !== faceId);
  }
  state._dismissedCount = Math.max(0, state._dismissedCount - 1);
  // Update pill count and restore-all button text
  const pill = document.querySelector('.people-filter-pill.active');
  if (pill) pill.textContent = `Ignored (${state._dismissedCount})`;
  const restoreBtn = document.querySelector('.dismissed-section-actions .btn');
  if (restoreBtn) restoreBtn.textContent = `Restore all (${state._dismissedCount})`;

  try {
    const resp = await apiFetch("/api/v1/faces/restore", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({face_ids: [faceId]}),
    });
    if (resp.albums) { state.albumList = resp.albums; renderAlbumNav(); }
    toast("Restored — will appear in People after re-clustering");
  } catch (e) {
    toastError("restore this face", e);
  }
}

export async function deleteFacePermanently(faceId) {
  try {
    await apiFetch("/api/v1/faces/purge", {
      method: "DELETE",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({face_ids: [faceId]}),
    });
    // Optimistic DOM removal (data-action button, not inline onclick).
    const btn = document.querySelector(`.dismissed-face-delete[data-arg0="${faceId}"]`);
    if (btn) btn.closest(".dismissed-face-cell")?.remove();
    if (state._dismissedFaces) {
      state._dismissedFaces = state._dismissedFaces.filter(f => f.face_id !== faceId);
    }
    state._dismissedCount = Math.max(0, state._dismissedCount - 1);
    const pill = document.querySelector('.people-filter-pill.active');
    if (pill) pill.textContent = `Ignored (${state._dismissedCount})`;
    toast("Face detection permanently deleted");
  } catch (e) {
    toastError("delete the person", e);
  }
}

export async function deleteAllDismissed() {
  if (!await appConfirm(
    `Permanently delete all ${state._dismissedCount} ignored face detection${state._dismissedCount !== 1 ? "s" : ""}?`,
    "This cannot be undone. Deleted detections will only reappear after re-analysis.",
    {okLabel: "Delete permanently", okClass: "danger"},
  )) return;
  try {
    const resp = await apiFetch("/api/v1/faces/purge", {
      method: "DELETE",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({all: true}),
    });
    const n = resp.deleted || 0;
    state._dismissedCount = 0;
    state._dismissedFaces = null;
    state.peopleFilter = "included";
    await loadFaceClusters();
    showPeopleView();
    toast(`Permanently deleted ${n} false detection${n !== 1 ? "s" : ""}`);
  } catch (e) {
    toastError("delete the person", e);
  }
}

export async function loadDismissedFaces() {
  if (state._dismissedFaces) return state._dismissedFaces;
  const data = await apiFetch("/api/v1/faces/dismissed?limit=80");
  state._dismissedFaces = data.faces || [];
  return state._dismissedFaces;
}

export async function expandDismissedSection() {
  const grid = document.getElementById("dismissed-faces-grid");
  if (!grid) return;
  if (grid.children.length > 0) return; // already loaded
  grid.innerHTML = '<div style="padding:8px;color:var(--text2)">Loading…</div>';
  const faces = await loadDismissedFaces();
  if (faces.length === 0) {
    grid.innerHTML = '<div style="padding:8px;color:var(--text2)">No faces to show</div>';
    return;
  }
  grid.innerHTML = "";
  for (const f of faces) {
    const cell = document.createElement("div");
    cell.className = "dismissed-face-cell";
    cell.innerHTML =
      `<img class="dismissed-face-img review-clickable" src="${authedSrc(`/api/v1/faces/crop/${escapeAttr(f.thumb_hash)}/${f.face_index}`)}" loading="lazy" data-action="openPhotoPreview" data-arg0="${escapeAttr(f.thumb_hash)}" data-arg1="${escapeAttr(f.filename || "")}" data-arg2="${escapeAttr(f.date || "")}" data-arg3="${escapeAttr(f.score == null ? "" : String(f.score))}" title="Click to see the full photo">` +
      `<div class="dismissed-face-actions">` +
      `<button class="dismissed-face-restore" data-stop-propagation="true" data-action="restoreFace" data-arg0="${f.face_id}" title="Restore this face">&#x21A9;</button>` +
      `<button class="dismissed-face-delete" data-stop-propagation="true" data-action="deleteFacePermanently" data-arg0="${f.face_id}" title="Delete permanently">\u2715</button>` +
      `</div>`;
    grid.appendChild(cell);
  }
}
