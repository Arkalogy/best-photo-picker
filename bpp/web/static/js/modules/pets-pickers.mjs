// @ts-check
/**
 * Pet picker overlays: identify (split a cluster into a new named one)
 * and merge (combine two clusters).
 *
 * Extracted from pets.mjs during the v0.1 cleanup. Both overlays follow
 * the same visual idiom as the people pickers (merge-picker classnames,
 * cancel/ok footer) and open through picker-overlay.mjs: a dialog with
 * focus handling and an Esc that closes only the picker.
 *
 * Re-exported from pets.mjs.
 */

import { apiFetch, authedSrc, petCropUrl } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import { openPicker, pickable } from "./picker-overlay.mjs";
import {
  _petDefaultLabel,
  loadPetClusters,
  petClassFromCluster,
  petDisplayName,
  showPetsView,
  startPetRename,
} from "./pets.mjs";


/**
 * @param {number} clusterId
 */
export async function showIdentifyPicker(clusterId) {
  const cls = petClassFromCluster(clusterId);
  const clusterName =
    petDisplayName(clusterId) || _petDefaultLabel(cls) || `Pet ${clusterId + 1}`;

  let detections, total;
  try {
    const data = await apiFetch(`/api/v1/pets/cluster/${clusterId}?limit=120`);
    detections = data.detections || [];
    total = data.total || detections.length;
  } catch (e) {
    toastError("load pet detections", e);
    return;
  }
  if (detections.length === 0) {
    toast("No detections in this group", true);
    return;
  }

  document.getElementById("identify-picker-overlay")?.remove();

  /** @type {Set<number>} */
  const selected = new Set();

  const overlay = document.createElement("div");
  overlay.id = "identify-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";
  picker.style.width = "min(600px, 90vw)";
  picker.style.maxHeight = "80vh";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  header.style.position = "relative";
  const sampleNote = total > detections.length ? ` (showing ${detections.length} of ${total})` : "";
  header.innerHTML = `Identify a pet in "${esc(clusterName)}"<small>Select all photos of the same individual pet${sampleNote}</small>`;
  const closeX = document.createElement("button");
  closeX.textContent = "×";
  closeX.style.cssText =
    "position:absolute;top:8px;right:8px;background:none;border:none;color:var(--text-secondary);font-size:22px;cursor:pointer;line-height:1;padding:2px 6px;border-radius:4px";
  closeX.onmouseenter = () => {
    closeX.style.color = "var(--text-primary)";
  };
  closeX.onmouseleave = () => {
    closeX.style.color = "var(--text-secondary)";
  };
  closeX.onclick = () => close();
  header.appendChild(closeX);
  picker.appendChild(header);

  const grid = document.createElement("div");
  grid.style.cssText =
    "display:flex;flex-wrap:wrap;gap:6px;padding:12px;max-height:480px;overflow-y:auto;justify-content:center";

  const confirmBtn = document.createElement("button");

  for (const d of detections) {
    grid.appendChild(_identifyCell(d, selected, confirmBtn));
  }
  picker.appendChild(grid);

  const footer = document.createElement("div");
  footer.className = "merge-picker-footer";
  footer.style.justifyContent = "space-between";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "merge-picker-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => close();

  confirmBtn.className = "btn btn-primary";
  confirmBtn.textContent = "Name this pet";
  confirmBtn.disabled = true;
  confirmBtn.onclick = () => _confirmIdentify(selected, detections, total, clusterId, close);

  footer.appendChild(cancelBtn);
  footer.appendChild(confirmBtn);
  picker.appendChild(footer);

  overlay.appendChild(picker);
  _installPreviewOnRightClick(grid);
  const close = openPicker(overlay, { label: `Identify a pet in "${clusterName}"` });
}

/**
 * One selectable crop in the identify grid.
 * @param {any} d
 * @param {Set<number>} selected
 * @param {HTMLButtonElement} confirmBtn
 */
function _identifyCell(d, selected, confirmBtn) {
  const cell = pickable(document.createElement("div"));
  cell.style.cssText =
    "cursor:pointer;border-radius:8px;overflow:hidden;border:2px solid transparent;transition:border-color .15s;width:88px;height:88px;flex-shrink:0";
  cell.dataset.detectionId = String(d.detection_id);
  cell.dataset.thumbHash = d.thumb_hash || "";
  const img = document.createElement("img");
  img.src = petCropUrl(d.thumb_hash, d.detection_index);
  img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
  img.loading = "lazy";
  cell.appendChild(img);

  cell.onclick = () => {
    const id = d.detection_id;
    if (selected.has(id)) {
      selected.delete(id);
      cell.style.borderColor = "transparent";
    } else {
      selected.add(id);
      cell.style.borderColor = "var(--accent)";
    }
    confirmBtn.disabled = selected.size === 0;
    confirmBtn.textContent =
      selected.size > 0 ? `Name this pet (${selected.size})` : "Name this pet";
  };
  return cell;
}

/**
 * Name the selected crops: the whole group is a rename; a subset is split
 * into a new group that is then named.
 * @param {Set<number>} selected
 * @param {any[]} detections
 * @param {number} total
 * @param {number} clusterId
 * @param {() => void} close
 */
async function _confirmIdentify(selected, detections, total, clusterId, close) {
  /** @type {any} */
  const win = window;
  if (selected.size === 0) return;
  close();
  if (selected.size === detections.length && total === detections.length) {
    const labelEl = document.getElementById(`pet-label-${clusterId}`);
    if (labelEl) startPetRename(clusterId, labelEl);
    return;
  }
  // In-progress in the status bar (indeterminate); the success toast below
  // is a "name it" prompt, not just a confirmation, so it stays.
  win.showStatusProgress?.("Splitting pet group…", 0);
  try {
    const resp = await apiFetch("/api/v1/pets/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detection_ids: [...selected] }),
    });
    if (resp.error) {
      toast(resp.error, true);
      return;
    }
    if (resp.albums) {
      win.albumList = resp.albums;
      win.renderAlbumNav?.();
    }
    await loadPetClusters();
    showPetsView();
    const newCid = resp.cluster_id;
    const labelEl = document.getElementById(`pet-label-${newCid}`);
    if (labelEl) {
      setTimeout(() => startPetRename(newCid, labelEl), 100);
    }
    toast("Pet identified — give it a name!");
  } catch (e) {
    toastError("split the pet group", e);
  } finally {
    win.hideStatusProgress?.();
  }
}

/**
 * Right-click a crop to see the full photo; Esc or click closes it.
 * @param {HTMLElement} grid
 */
function _installPreviewOnRightClick(grid) {
  /** @type {HTMLElement | null} */
  let previewEl = null;
  function closePreview() {
    if (!previewEl) return;
    document.removeEventListener("keydown", previewKey, true);
    previewEl.remove();
    previewEl = null;
  }
  /** @param {KeyboardEvent} ev */
  function previewKey(ev) {
    if (ev.key === "Escape") {
      ev.stopPropagation();
      ev.stopImmediatePropagation();
      closePreview();
    }
  }
  grid.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    const target = /** @type {HTMLElement} */ (ev.target);
    const cell = target.closest("[data-detection-id]");
    if (!cell) return;
    const img = cell.querySelector("img");
    if (!img) return;
    closePreview();
    previewEl = document.createElement("div");
    previewEl.style.cssText =
      "position:fixed;inset:0;z-index:10002;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center";
    const closeBtn = document.createElement("button");
    closeBtn.textContent = "×";
    closeBtn.style.cssText =
      "position:absolute;top:16px;right:20px;background:none;border:none;color:#fff;font-size:32px;cursor:pointer;opacity:0.7;line-height:1;padding:4px 8px";
    closeBtn.onmouseenter = () => {
      closeBtn.style.opacity = "1";
    };
    closeBtn.onmouseleave = () => {
      closeBtn.style.opacity = "0.7";
    };
    closeBtn.onclick = (e) => {
      e.stopPropagation();
      closePreview();
    };
    previewEl.appendChild(closeBtn);
    const big = document.createElement("img");
    const th = /** @type {HTMLElement} */ (cell).dataset.thumbHash;
    big.src = th ? authedSrc("/thumb/" + th) : /** @type {HTMLImageElement} */ (img).src;
    big.style.cssText =
      "max-width:92vw;max-height:92vh;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.5)";
    previewEl.appendChild(big);
    previewEl.onclick = closePreview;
    document.body.appendChild(previewEl);
    document.addEventListener("keydown", previewKey, true);
  });
}

/**
 * @param {number} sourceClusterId
 */
export function showPetMergePicker(sourceClusterId) {
  /** @type {any} */
  const win = window;
  const cls = petClassFromCluster(sourceClusterId);
  const sourceName =
    petDisplayName(sourceClusterId) || _petDefaultLabel(cls) || `Pet ${sourceClusterId + 1}`;

  document.getElementById("pet-merge-picker-overlay")?.remove();

  const overlay = document.createElement("div");
  overlay.id = "pet-merge-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  header.innerHTML = `Merge "${esc(sourceName)}" into…<small>Choose the pet group to keep</small>`;
  picker.appendChild(header);

  const list = document.createElement("div");
  list.className = "merge-picker-list";

  const clusters = /** @type {any[]} */ (win.petClusters || []);
  const sorted = [...clusters]
    .filter((c) => c.cluster_id !== sourceClusterId)
    .sort((a, b) => {
      const na = petDisplayName(a.cluster_id) || "";
      const nb = petDisplayName(b.cluster_id) || "";
      if (na && !nb) return -1;
      if (!na && nb) return 1;
      return b.photo_count - a.photo_count;
    });

  for (const c of sorted) {
    const cid = c.cluster_id;
    const pcls = petClassFromCluster(cid);
    const name = petDisplayName(cid) || _petDefaultLabel(pcls) || `Pet ${cid + 1}`;
    const item = pickable(document.createElement("div"));
    item.className = "merge-picker-item";
    const rep = c.representative;
    const avatarUrl =
      rep && rep.thumb_hash
        ? petCropUrl(rep.thumb_hash, rep.detection_index)
        : "";
    item.innerHTML =
      (avatarUrl
        ? `<img class="merge-picker-avatar" src="${avatarUrl}" alt="">`
        : `<div class="merge-picker-avatar"></div>`) +
      `<div class="merge-picker-info"><div class="merge-picker-name">${esc(name)}</div>` +
      `<div class="merge-picker-count">${c.photo_count} photo${c.photo_count === 1 ? "" : "s"}</div></div>`;
    item.onclick = async () => {
      close();
      const targetName = petDisplayName(cid) || _petDefaultLabel(pcls) || `Pet ${cid + 1}`;
      const ok = await appConfirm(`Merge "${sourceName}" into "${targetName}"?`, {
        okLabel: "Merge",
      });
      if (!ok) return;
      // Progress in the status bar; no "done" toast — the merged result is
      // visible (two groups become one in the list).
      win.showStatusProgress?.("Merging pet groups…", 0);
      try {
        const resp = await apiFetch("/api/v1/pets/merge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            primary_cluster_id: cid,
            merge_cluster_ids: [sourceClusterId],
          }),
        });
        if (resp.error) {
          toast(resp.error, true);
          return;
        }
        if (resp.albums) {
          win.albumList = resp.albums;
          win.renderAlbumNav?.();
        }
        await loadPetClusters();
        showPetsView();
      } catch (e) {
        toastError("merge the pet groups", e);
      } finally {
        win.hideStatusProgress?.();
      }
    };
    list.appendChild(item);
  }

  if (list.children.length === 0) {
    list.innerHTML =
      '<div class="merge-picker-empty">No other pet groups to merge with</div>';
  }

  picker.appendChild(list);

  const footer = document.createElement("div");
  footer.className = "merge-picker-footer";
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "merge-picker-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => close();
  footer.appendChild(cancelBtn);
  picker.appendChild(footer);

  overlay.appendChild(picker);
  const close = openPicker(overlay, { label: `Merge "${sourceName}"` });
}
