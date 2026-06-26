// @ts-check
/**
 * Person picker modals: split, merge, avatar.
 *
 * Extracted from people.mjs during the v0.1 cleanup. Owns the three
 * full-screen picker overlays. Re-exported from people.mjs so the
 * bridge keeps exposing every data-action handler on window unchanged.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { loadAlbumList, switchAlbum } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { toast, toastError } from "./toast.mjs";
import { doMerge, personDisplayName, showPeopleView } from "./people.mjs";
import { _selectedPeople, mergeSelected } from "./people-merge.mjs";


export async function showSplitPicker(clusterId) {
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  let faces;
  try {
    const data = await apiFetch(`/api/v1/faces/cluster/${clusterId}`);
    faces = data.faces || [];
  } catch (e) {
    toastError("load faces", e);
    return;
  }
  if (faces.length < 2) {
    toast("Need at least 2 faces to split");
    return;
  }

  const existing = document.getElementById("split-picker-overlay");
  if (existing) existing.remove();

  const selected = new Set();

  const overlay = document.createElement("div");
  overlay.id = "split-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";
  picker.style.width = "min(540px, 90vw)";
  picker.style.maxHeight = "80vh";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  header.innerHTML = `Split "${esc(name)}"<small>Click the wrong faces, then split them into a new person</small>`;
  picker.appendChild(header);

  const grid = document.createElement("div");
  grid.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;padding:12px;max-height:400px;overflow-y:auto;justify-content:center";

  for (const f of faces) {
    const cell = document.createElement("div");
    cell.className = "face-select-cell";
    cell.style.cssText = "cursor:pointer;border-radius:8px;overflow:hidden;border:3px solid transparent;transition:border-color .15s;width:88px;height:88px;flex-shrink:0";
    const img = document.createElement("img");
    img.src = authedSrc(`/api/v1/faces/crop/${esc(f.thumb_hash)}/${f.face_index}`);
    img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
    img.loading = "lazy";
    cell.appendChild(img);
    cell.onclick = () => {
      const key = `${f.filepath}:${f.face_index}`;
      if (selected.has(key)) {
        selected.delete(key);
        cell.style.borderColor = "transparent";
      } else {
        selected.add(key);
        cell.style.borderColor = "var(--accent)";
      }
      cell.dataset.filepath = f.filepath;
      cell.dataset.faceIndex = f.face_index;
      splitBtn.disabled = selected.size === 0 || selected.size === faces.length;
      splitBtn.textContent = `Split ${selected.size} face${selected.size !== 1 ? "s" : ""} out`;
    };
    grid.appendChild(cell);
  }
  picker.appendChild(grid);

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;gap:8px;padding:12px;justify-content:flex-end";

  // Merge-into input: type a name to split + merge in one step
  const mergeRow = document.createElement("div");
  mergeRow.style.cssText = "padding:0 12px 8px;display:flex;gap:6px;align-items:center;position:relative";
  const mergeInput = document.createElement("input");
  mergeInput.type = "text";
  mergeInput.placeholder = "Or merge into existing person\u2026";
  mergeInput.style.cssText = "flex:1;padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:12px";
  const mergeDropdown = document.createElement("div");
  mergeDropdown.className = "review-autocomplete";
  mergeDropdown.style.cssText = "position:absolute;top:100%;left:12px;right:12px;z-index:10";
  let splitMergeTarget = null;
  mergeInput.oninput = () => {
    const q = mergeInput.value.trim().toLowerCase();
    splitMergeTarget = null;
    if (q.length < 1) { mergeDropdown.innerHTML = ""; return; }
    const matches = state.faceClusters
      .filter(c => c.cluster_id !== clusterId && (personDisplayName(c.cluster_id) || "").toLowerCase().includes(q))
      .slice(0, 6);
    mergeDropdown.innerHTML = matches.map(c => {
      const n = personDisplayName(c.cluster_id) || `Person ${c.cluster_id + 1}`;
      const rep = c.representative;
      const src = authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`);
      return `<div class="review-ac-item" style="display:flex;align-items:center;gap:8px;padding:6px 8px;cursor:pointer" data-cid="${c.cluster_id}"><img src="${escapeAttr(src)}" style="width:28px;height:28px;border-radius:50%;object-fit:cover"><span>${esc(n)}</span></div>`;
    }).join("");
    /** @type {NodeListOf<HTMLElement>} */ (
      mergeDropdown.querySelectorAll(".review-ac-item")
    ).forEach(item => {
      item.onclick = () => {
        splitMergeTarget = Number(item.dataset.cid);
        const n = personDisplayName(splitMergeTarget) || `Person ${splitMergeTarget + 1}`;
        mergeInput.value = n;
        mergeDropdown.innerHTML = "";
        splitBtn.textContent = `Split & merge into "${n}"`;
      };
    });
  };
  mergeRow.appendChild(mergeInput);
  mergeRow.appendChild(mergeDropdown);
  picker.appendChild(mergeRow);

  const splitBtn = document.createElement("button");
  splitBtn.className = "btn btn-primary";
  splitBtn.textContent = "Split 0 faces out";
  splitBtn.disabled = true;
  splitBtn.onclick = async () => {
    await splitSelectedFaces(clusterId, grid, selected, splitMergeTarget);
    overlay.remove();
  };
  actions.appendChild(splitBtn);

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-secondary";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => overlay.remove();
  actions.appendChild(cancelBtn);

  picker.appendChild(actions);
  overlay.appendChild(picker);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}

export async function splitSelectedFaces(clusterId, grid, selectedKeys, mergeTargetCid) {
  const sourceName = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  // Resolve face_ids from the cluster detail
  try {
    const data = await apiFetch(`/api/v1/faces/cluster/${clusterId}?limit=999`);
    const faces = data.faces || [];
    const faceIds = [];
    for (const f of faces) {
      const key = `${f.filepath}:${f.face_index}`;
      if (selectedKeys.has(key)) faceIds.push(f.face_id);
    }
    if (faceIds.length === 0) {
      toast("No matching faces found", true);
      return;
    }
    const n = faceIds.length;
    const photos = `${n} photo${n !== 1 ? "s" : ""}`;
    const resp = await apiFetch("/api/v1/faces/split", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ face_ids: faceIds }),
    });
    // If merge target specified, merge the new cluster into it
    if (mergeTargetCid != null && resp.new_cluster_id != null) {
      await apiFetch("/api/v1/faces/merge", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          primary_cluster_id: mergeTargetCid,
          merge_cluster_ids: [resp.new_cluster_id],
        }),
      });
      const targetName = personDisplayName(mergeTargetCid) || `Person ${mergeTargetCid + 1}`;
      toast(`Moved ${photos} from ${sourceName} to ${targetName}`);
    } else {
      toast(`Removed ${photos} from ${sourceName} and created a new person`);
    }
    await loadAlbumList();
    await loadFaceClusters();
    // Stay in person album if we were viewing one, otherwise go to Faces
    if (state.currentView === "album" && state.currentAlbumId) {
      switchAlbum(state.currentAlbumId, {force: true});
    } else {
      showPeopleView();
    }
  } catch (e) {
    toastError("split the group", e);
  }
}

// ── Faces: merge picker modal ──
export function showMergePicker(sourceClusterId) {
  state.mergeSourceId = sourceClusterId;
  const sourceName = personDisplayName(sourceClusterId) || `Person ${sourceClusterId + 1}`;
  // Bulk mode: the right-clicked / dropped person is part of a multi-selection.
  // The chosen target then absorbs EVERY selected person, not just this one.
  const bulk = _selectedPeople.size > 1 && _selectedPeople.has(sourceClusterId);

  // Remove existing picker
  const existing = document.getElementById("merge-picker-overlay");
  if (existing) existing.remove();

  // Build overlay
  const overlay = document.createElement("div");
  overlay.id = "merge-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";

  // Header
  const header = document.createElement("div");
  header.className = "merge-picker-header";
  header.innerHTML = bulk
    ? `Merge ${_selectedPeople.size} people into\u2026<small>Choose the person to keep</small>`
    : `Merge "${esc(sourceName)}" into\u2026<small>Choose the person to keep</small>`;
  picker.appendChild(header);

  // Search
  const search = document.createElement("input");
  search.type = "text";
  search.placeholder = "Search people\u2026";
  search.className = "merge-picker-search";
  picker.appendChild(search);

  // List
  const list = document.createElement("div");
  list.className = "merge-picker-list";

  function renderList(filter) {
    list.innerHTML = "";
    const q = (filter || "").toLowerCase();
    const sorted = [...state.faceClusters]
      .filter(c => c.cluster_id !== sourceClusterId)
      .sort((a, b) => {
        const na = personDisplayName(a.cluster_id) || "";
        const nb = personDisplayName(b.cluster_id) || "";
        if (na && !nb) return -1;
        if (!na && nb) return 1;
        return b.photo_count - a.photo_count;
      });
    for (const c of sorted) {
      const name = personDisplayName(c.cluster_id) || `Person ${c.cluster_id + 1}`;
      if (q && !name.toLowerCase().includes(q)) continue;
      const item = document.createElement("div");
      item.className = "merge-picker-item";
      const rep = c.representative;
      const avatarUrl = rep && rep.thumb_hash
        ? authedSrc(`/api/v1/faces/crop/${rep.thumb_hash}/${rep.face_index}`) : "";
      item.innerHTML =
        (avatarUrl ? `<img class="merge-picker-avatar" src="${avatarUrl}" alt="">` : `<div class="merge-picker-avatar"></div>`) +
        `<div class="merge-picker-info"><div class="merge-picker-name">${esc(name)}</div>` +
        `<div class="merge-picker-count">${c.photo_count} photo${c.photo_count === 1 ? '' : 's'}</div></div>`;
      item.onclick = async () => {
        closeMergePicker();
        if (bulk) {
          // mergeSelected() builds the chip-confirm, drops the target from the
          // merge set, clears the selection, and calls doMerge.
          await mergeSelected(c.cluster_id);
          return;
        }
        const targetName = personDisplayName(c.cluster_id) || `Person ${c.cluster_id + 1}`;
        if (!await appConfirm(`Merge "${sourceName}" into "${targetName}"?`, {okLabel: "Merge"})) return;
        await doMerge(c.cluster_id, [sourceClusterId]);
      };
      list.appendChild(item);
    }
    if (list.children.length === 0) {
      list.innerHTML = '<div class="merge-picker-empty">No other people found</div>';
    }
  }

  search.addEventListener("input", () => renderList(search.value));
  search.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Escape") closeMergePicker();
  });
  picker.appendChild(list);
  renderList("");

  // Footer with cancel
  const footer = document.createElement("div");
  footer.className = "merge-picker-footer";
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "merge-picker-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = closeMergePicker;
  footer.appendChild(cancelBtn);
  picker.appendChild(footer);

  overlay.appendChild(picker);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) closeMergePicker();
  });

  document.body.appendChild(overlay);
  setTimeout(() => search.focus(), 50);
}

export function closeMergePicker() {
  state.mergeSourceId = null;
  const overlay = document.getElementById("merge-picker-overlay");
  if (overlay) overlay.remove();
}

// ── Faces: avatar picker ──
export async function showAvatarPicker(clusterId) {
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;

  // Fetch faces in this cluster (sampled if large)
  let faces, totalFaces;
  try {
    const data = await apiFetch(`/api/v1/faces/cluster/${clusterId}`);
    faces = data.faces || [];
    totalFaces = data.total || faces.length;
  } catch (e) {
    toastError("load faces", e);
    return;
  }
  if (faces.length === 0) {
    toast("No faces found for this person", true);
    return;
  }

  // Remove existing picker
  const existing = document.getElementById("avatar-picker-overlay");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "avatar-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";
  picker.style.width = "min(540px, 90vw)";
  picker.style.maxHeight = "80vh";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  const sampleNote = totalFaces > faces.length ? ` (showing ${faces.length} of ${totalFaces})` : "";
  header.innerHTML = `Choose avatar for "${esc(name)}"<small>Click a face to set it as the avatar${sampleNote}</small>`;
  picker.appendChild(header);

  const grid = document.createElement("div");
  grid.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;padding:12px;max-height:480px;overflow-y:auto;justify-content:center";

  for (const f of faces) {
    const cell = document.createElement("div");
    cell.style.cssText = "cursor:pointer;border-radius:8px;overflow:hidden;border:2px solid transparent;transition:border-color .15s;width:88px;height:88px;flex-shrink:0";
    const img = document.createElement("img");
    img.src = authedSrc(`/api/v1/faces/crop/${esc(f.thumb_hash)}/${f.face_index}`);
    img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
    img.loading = "lazy";
    cell.appendChild(img);
    cell.addEventListener("mouseenter", () => { cell.style.borderColor = "var(--accent)"; });
    cell.addEventListener("mouseleave", () => { cell.style.borderColor = "transparent"; });
    cell.onclick = async () => {
      overlay.remove();
      try {
        await apiFetch("/api/v1/faces/avatar", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({cluster_id: clusterId, filepath: f.filepath, face_index: f.face_index}),
        });
        await loadFaceClusters();
        showPeopleView();
        toast("Avatar updated");
      } catch (e) {
        toastError("set the avatar", e);
      }
    };
    grid.appendChild(cell);
  }
  picker.appendChild(grid);

  const footer = document.createElement("div");
  footer.className = "merge-picker-footer";
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "merge-picker-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => overlay.remove();
  footer.appendChild(cancelBtn);

  // Reset button to go back to auto-picked avatar
  const resetBtn = document.createElement("button");
  resetBtn.className = "merge-picker-cancel";
  resetBtn.textContent = "Reset to auto";
  resetBtn.style.marginRight = "auto";
  resetBtn.onclick = async () => {
    overlay.remove();
    try {
      await apiFetch("/api/v1/faces/avatar", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({cluster_id: clusterId, filepath: null, face_index: null}),
      });
      await loadFaceClusters();
      showPeopleView();
      toast("Avatar reset to auto");
    } catch (e) {
      toastError("reset the avatar", e);
    }
  };
  footer.insertBefore(resetBtn, cancelBtn);
  picker.appendChild(footer);

  overlay.appendChild(picker);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
}
