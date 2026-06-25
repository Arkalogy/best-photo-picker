// @ts-check
/**
 * Pet clusters: loading, naming/rename, view rendering, identify-picker
 * (split a cluster into a new named one), merge-picker, and the right-
 * click context menu.
 *
 * `petClusters` is now a `window` property (declared in globals.js with
 * no `let` so classic-side bare reads + module-side writes both work
 * via the global-object scope-chain fallback).
 *
 * Cross-file callees are looked up on `window` (`renderAlbumNav`,
 * `loadAlbumList`, `refreshSmartAlbums` (also imported), `navigateTo`).
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc } from "./text-format.mjs";
import { refreshSmartAlbums } from "./faces.mjs";
import { show } from "./utils.mjs";
import { toast, toastError } from "./toast.mjs";
import { updateToolbarTitle } from "./core.mjs";

/**
 * Refresh `petClusters` from the server.
 *
 * Protection B: routed through wrapSectionLoader so a /api/v1/pets
 * failure surfaces as a retry pill in the Pets sidebar section
 * instead of silently leaving the cluster list empty. The wrapper
 * never throws; if the call fails, `petClusters` is set to the
 * empty array first so downstream code never reads `undefined`.
 */
export async function loadPetClusters() {
  /** @type {any} */
  const win = window;
  if (!Array.isArray(win.petClusters)) win.petClusters = [];
  const { wrapSectionLoader } = await import("./sidebar-safety.mjs");
  return wrapSectionLoader("pets", _loadPetClustersInner);
}

async function _loadPetClustersInner() {
  /** @type {any} */
  const win = window;
  const data = await apiFetch("/api/v1/pets/clusters");
  win.petClusters = data.clusters || [];
}

/**
 * @param {number} clusterId
 * @returns {any}
 */
function _findPetAlbum(clusterId) {
  /** @type {any} */
  const win = window;
  const albums = /** @type {any[]} */ (win.albumList || []);
  return (
    albums.find(
      (a) => a.album_type === "smart_pet" && a.rule && a.rule.cluster_id === clusterId
    ) ||
    albums.find(
      (a) =>
        a.album_type === "smart_pet" &&
        a.rule &&
        a.rule.pet_class === petClassFromCluster(clusterId)
    )
  );
}

/**
 * @param {number} clusterId
 */
export function getPetName(clusterId) {
  const album = _findPetAlbum(clusterId);
  return album ? album.name : null;
}

/**
 * @param {number} clusterId
 */
export function getPetAlbumId(clusterId) {
  const album = _findPetAlbum(clusterId);
  return album ? album.id : null;
}

/**
 * @param {number} clusterId
 */
export function petClassFromCluster(clusterId) {
  /** @type {any} */
  const win = window;
  const clusters = /** @type {any[]} */ (win.petClusters || []);
  const c = clusters.find((c) => c.cluster_id === clusterId);
  return c ? c.pet_class : null;
}

/**
 * @param {string | null} cls
 */
export function _petDefaultLabel(cls) {
  if (!cls) return null;
  const singular = cls.replace(/s$/, "");
  return singular.charAt(0).toUpperCase() + singular.slice(1) + "s";
}

/**
 * @param {number} clusterId
 */
export function petDisplayName(clusterId) {
  const name = getPetName(clusterId);
  const cls = petClassFromCluster(clusterId);
  const defaultName = _petDefaultLabel(cls) || `Pet ${clusterId + 1}`;
  if (!name || name === defaultName) return null;
  return name;
}

/**
 * @param {number} clusterId
 * @param {string} newName
 */
export async function renamePet(clusterId, newName) {
  /** @type {any} */
  const win = window;
  const albumId = getPetAlbumId(clusterId);
  if (!albumId) return;
  const trimmed = newName.trim();
  if (!trimmed) return;
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    await win.loadAlbumList?.();
    showPetsView();
  } catch (e) {
    toastError("rename this pet", e);
  }
}

/**
 * @param {number} clusterId
 * @param {HTMLElement} el
 */
export function startPetRename(clusterId, el) {
  const cls = petClassFromCluster(clusterId);
  const current = petDisplayName(clusterId) || _petDefaultLabel(cls) || "";
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.placeholder = "Name this pet";
  input.className = "inline-rename-input";
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") el.innerHTML = petLabelHTML(clusterId);
  });
  input.addEventListener("blur", () => {
    if (input.value.trim()) {
      renamePet(clusterId, input.value);
    } else {
      el.innerHTML = petLabelHTML(clusterId);
    }
  });
  el.innerHTML = "";
  el.appendChild(input);
  input.focus();
  input.select();
}

/**
 * @param {number} clusterId
 * @param {number} [photoCount]
 */
export function petLabelHTML(clusterId, photoCount) {
  /** @type {any} */
  const win = window;
  const name = petDisplayName(clusterId);
  const clusters = /** @type {any[]} */ (win.petClusters || []);
  const cluster = clusters.find((c) => c.cluster_id === clusterId);
  const count = photoCount ?? (cluster ? cluster.photo_count : 0);
  const cls = petClassFromCluster(clusterId);
  const defaultName = _petDefaultLabel(cls) || `Pet ${clusterId + 1}`;
  const displayName = name || defaultName;
  const nameCls = name ? "person-name" : "person-name unnamed";
  return (
    `<div class="${nameCls}" data-stop-propagation="true" data-action="startPetRename" data-arg0="${clusterId}" data-arg1="this.parentElement">${esc(displayName)}</div>` +
    `<div class="person-count">${count} photo${count === 1 ? "" : "s"}</div>`
  );
}

export function showPetsView() {
  /** @type {any} */
  const win = window;
  win.currentAlbumId = null;
  const content = document.querySelector(".content");
  let view = document.getElementById("pets-view");
  if (!view) {
    view = document.createElement("div");
    view.id = "pets-view";
    view.className = "people-grid";
    if (content) content.appendChild(view);
  }
  view.classList.remove("hidden");
  show("toolbar");
  show("status-bar");

  const ICONS = win.ICONS || {};
  const clusters = /** @type {any[]} */ (win.petClusters || []);
  if (clusters.length === 0) {
    const msg = !win.petsAvailable
      ? `Pet detection is not installed.<br><code class="install-hint">pip install bppicker[pets]</code>`
      : "Import and analyze photos to detect pets.";
    view.innerHTML = `<div class="empty-state people-empty">
      <div class="icon">${ICONS.paw || ""}</div>
      <div class="title">No Pets Found</div>
      <div class="desc">${msg}</div>
    </div>`;
    // Even with an empty view, the toolbar title needs to flip to
    // "Pets" so the user can tell the navigation actually happened —
    // otherwise the title stays at the previous view ("Library") which
    // reads as "click did nothing."
    updateToolbarTitle("Pets", "0 pet groups");
    return;
  }

  const visible = [...clusters].sort((a, b) => b.photo_count - a.photo_count);

  const cards = visible
    .map((c) => {
      const rep = c.representative;
      const cid = c.cluster_id;
      const cropSrc =
        rep && rep.thumb_hash
          ? authedSrc(`/api/v1/pets/crop/${esc(rep.thumb_hash)}/${rep.detection_index}`)
          : "";
      return `<div class="person-card pet-card" data-cluster-id="${cid}"
      data-action="navigateToPetAlbum" data-arg0="${cid}"
      data-oncontextmenu="showPetCtxMenu" data-arg0="${cid}">
      <div class="person-avatar pet-avatar">
        ${cropSrc ? `<img src="${cropSrc}" loading="lazy" draggable="false">` : `<div class="pet-avatar-icon">${ICONS.paw || ""}</div>`}
      </div>
      <div class="person-label" id="pet-label-${cid}">${petLabelHTML(cid, c.photo_count)}</div>
    </div>`;
    })
    .join("");

  view.innerHTML = cards;

  const subtitle = `${clusters.length} pet group${clusters.length === 1 ? "" : "s"}`;
  const summary = document.getElementById("status-summary");
  if (summary) summary.textContent = subtitle;
  updateToolbarTitle("Pets", subtitle);
}

/**
 * @param {number} clusterId
 */
export async function navigateToPetAlbum(clusterId) {
  /** @type {any} */
  const win = window;
  let petAlbum = _findPetAlbum(clusterId);
  if (!petAlbum) {
    await refreshSmartAlbums();
    petAlbum = _findPetAlbum(clusterId);
  }
  if (petAlbum) {
    win.navigateTo?.("album", petAlbum.id);
  } else {
    toast("Pet album not found — try re-analyzing", true);
  }
}

export function navigateToPets() {
  /** @type {any} */
  const win = window;
  win.navigateTo?.("pets");
}

/** @type {number | null} */
let petCtxClusterId = null;

/**
 * @param {MouseEvent} e
 * @param {number} clusterId
 */
export function showPetCtxMenu(e, clusterId) {
  e.preventDefault();
  e.stopPropagation();
  petCtxClusterId = clusterId;
  const menu = /** @type {HTMLElement | null} */ (document.getElementById("pet-ctx-menu"));
  if (!menu) return;
  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  menu.classList.remove("hidden");
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth)
      menu.style.left = window.innerWidth - rect.width - 8 + "px";
    if (rect.bottom > window.innerHeight)
      menu.style.top = window.innerHeight - rect.height - 8 + "px";
  });
}

export function hidePetCtxMenu() {
  document.getElementById("pet-ctx-menu")?.classList.add("hidden");
  petCtxClusterId = null;
}

export function initPetCtxMenu() {
  document.addEventListener("click", () => hidePetCtxMenu());
  const menu = document.getElementById("pet-ctx-menu");
  if (!menu) return;
  menu.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement | null} */ (e.target);
    const item = target?.closest(".ctx-menu-item");
    if (!item || petCtxClusterId === null) return;
    const action = /** @type {HTMLElement} */ (item).dataset.action;
    const cid = petCtxClusterId;
    hidePetCtxMenu();

    if (action === "rename") {
      const label = document.getElementById(`pet-label-${cid}`);
      if (label) startPetRename(cid, label);
    } else if (action === "identify") {
      showIdentifyPicker(cid);
    } else if (action === "merge") {
      showPetMergePicker(cid);
    } else if (action === "not-a-pet") {
      dismissPetCluster(cid);
    }
  });
}

/**
 * "Not a pet" — mark every detection in the cluster as a false detection.
 * Removes the group from the Pets view, photo chips, and pet albums.
 * @param {number} clusterId
 */
export async function dismissPetCluster(clusterId) {
  /** @type {any} */
  const win = window;
  const clusters = /** @type {any[]} */ (win.petClusters || []);
  const c = clusters.find((c) => c.cluster_id === clusterId);
  const name = petDisplayName(clusterId) || _petDefaultLabel(petClassFromCluster(clusterId)) || "this group";
  const photoCount = c?.photo_count || 0;
  const ok = await appConfirm(
    `Not a pet: "${name}"?`,
    `Marks these detections as false — the group disappears from Pets, photo chips, and its album. Photos themselves are untouched.${photoCount ? ` Affects ${photoCount} photo${photoCount === 1 ? "" : "s"}.` : ""}`,
    { okLabel: "Not a pet", okClass: "danger" },
  );
  if (!ok) return;
  try {
    const resp = await apiFetch("/api/v1/pets/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cluster_id: clusterId }),
    });
    if (resp.albums) {
      win.albumList = resp.albums;
      win.renderAlbumNav?.();
    }
    await loadPetClusters();
    showPetsView();
    toast(`"${name}" marked not a pet`);
  } catch (e) {
    toastError(`dismiss "${name}"`, e);
  }
}


/** Test-only: read internal context-menu cluster ID. */
export function _getPetCtxClusterId() {
  return petCtxClusterId;
}

/** Test-only: reset internal context-menu state. */
export function _resetPetsState() {
  petCtxClusterId = null;
}

import { showIdentifyPicker, showPetMergePicker } from "./pets-pickers.mjs";
export { showIdentifyPicker, showPetMergePicker };
