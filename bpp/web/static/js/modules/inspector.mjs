// @ts-check
/**
 * Photo info-panel helpers used by the lightbox info pane and a
 * couple of grid menu items:
 *  - face chip loader for the inspector pane
 *  - tag picker popover for "tag a person"
 *  - rename / untag flows
 *  - copy-path-to-clipboard helper
 *  - small EXIF formatters (camera / size)
 *
 * Reads the global `faceClusters` + `currentGridItems` + `lightboxIdx`
 * via window since those still live in classic land. Calls
 * `personDisplayName` / `getPersonAlbumId` / `loadAlbumList` /
 * `updateLightboxFaces` similarly.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appPrompt } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * Load and render the face chips for a photo into `#iph-face-chips`.
 * Includes detected faces, manual person tags, and a "+ tag a person"
 * trailing chip.
 *
 * @param {string} thumbHash
 */
export async function _iphLoadFaces(thumbHash) {
  /** @type {any} */
  const win = window;
  try {
    const data = await apiFetch(`/api/v1/faces/photo/${thumbHash}`);
    const container = document.getElementById("iph-face-chips");
    if (!container) return;

    const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
    let html = "";

    if (data.faces && data.faces.length > 0) {
      for (const f of data.faces) {
        const cropUrl = authedSrc(`/api/v1/faces/crop/${thumbHash}/${f.face_index}`);
        const label = f.name || `Face ${f.face_index + 1}`;
        const small = f.bbox_w != null && f.bbox_w < 40;
        const clickable = f.cluster_id != null;
        html += `<div class="iph-face-chip${small ? " iph-face-small" : ""}" title="${escapeAttr(label + (small ? " (small)" : "") + (clickable ? " — click to rename" : ""))}">`;
        html += `<img class="iph-face-img" src="${cropUrl}" alt="" loading="lazy">`;
        html += clickable
          ? `<span class="iph-face-label iph-face-rename" data-action="iphRenameFace" data-pass-event="true" data-arg0="${f.cluster_id}">${esc(label)}</span>`
          : `<span class="iph-face-label">${esc(label)}</span>`;
        html += `</div>`;
      }
    }

    if (data.person_tags && data.person_tags.length > 0) {
      for (const pt of data.person_tags) {
        const label = pt.name || `Person ${pt.cluster_id + 1}`;
        const cluster = faceClusters.find((c) => c.cluster_id === pt.cluster_id);
        const rep = cluster && cluster.representative;
        const avatarUrl = rep
          ? authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`)
          : "";
        html += `<div class="iph-face-chip iph-face-tagged" title="${escapeAttr(label + " (tagged)")}">`;
        if (avatarUrl) {
          html += `<img class="iph-face-img" src="${avatarUrl}" alt="" loading="lazy">`;
        } else {
          html += `<span class="iph-face-img iph-face-placeholder"></span>`;
        }
        html += `<span class="iph-face-label iph-face-rename" data-action="iphRenameFace" data-pass-event="true" data-arg0="${pt.cluster_id}">${esc(label)}</span>`;
        html += `<span class="iph-face-untag" title="Remove tag" data-action="_iphUntagPerson" data-pass-event="true" data-arg0="${thumbHash}" data-arg1="${pt.cluster_id}">&times;</span>`;
        html += `</div>`;
      }
    }

    if (faceClusters.length > 0) {
      html += `<div class="iph-face-chip iph-face-add" title="Tag a person" data-action="_iphShowTagPicker" data-pass-event="true" data-arg0="${thumbHash}">`;
      html += `<span class="iph-face-add-icon">+</span>`;
      html += `</div>`;
    }
    container.innerHTML = html || '<span class="iph-meta-val">No faces detected</span>';
  } catch {
    const container = document.getElementById("iph-face-chips");
    if (container) {
      container.innerHTML = '<span class="iph-meta-val">Failed to load faces</span>';
    }
  }
}

/**
 * Remove a manual person tag and refresh the chip strip + the
 * lightbox face section if it's open.
 *
 * @param {MouseEvent} e
 * @param {string} thumbHash
 * @param {number} clusterId
 */
export async function _iphUntagPerson(e, thumbHash, clusterId) {
  e.stopPropagation();
  /** @type {any} */
  const win = window;
  try {
    await apiFetch("/api/v1/faces/tag", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path_hash: thumbHash, cluster_id: clusterId }),
    });
    toast("Person tag removed");
    _iphLoadFaces(thumbHash);
    const idx = /** @type {number} */ (win.lightboxIdx);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (typeof idx === "number" && idx >= 0 && idx < items.length) {
      win.updateLightboxFaces?.(items[idx]);
    }
  } catch (err) {
    toastError("remove this person tag", err);
  }
}

/**
 * Build the tag-picker popover at click coords. Lists known people
 * (named first, then by photo_count desc), supports a search filter,
 * Esc / outside-click to dismiss.
 *
 * @param {MouseEvent} e
 * @param {string} thumbHash
 */
export function _iphShowTagPicker(e, thumbHash) {
  e.stopPropagation();
  /** @type {any} */
  const win = window;
  document.getElementById("iph-tag-picker")?.remove();

  const picker = document.createElement("div");
  picker.id = "iph-tag-picker";
  picker.className = "iph-tag-picker";

  const search = document.createElement("input");
  search.type = "text";
  search.placeholder = "Search people…";
  search.className = "iph-tag-search";
  picker.appendChild(search);

  const list = document.createElement("div");
  list.className = "iph-tag-list";

  /** @param {string} filter */
  function renderList(filter) {
    list.innerHTML = "";
    const q = (filter || "").toLowerCase();
    const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
    const sorted = [...faceClusters].sort((a, b) => {
      const na = win.personDisplayName?.(a.cluster_id) || "";
      const nb = win.personDisplayName?.(b.cluster_id) || "";
      if (na && !nb) return -1;
      if (!na && nb) return 1;
      return b.photo_count - a.photo_count;
    });
    for (const c of sorted) {
      const name = win.personDisplayName?.(c.cluster_id) || `Person ${c.cluster_id + 1}`;
      if (q && !name.toLowerCase().includes(q)) continue;
      const item = document.createElement("div");
      item.className = "iph-tag-item";
      const rep = c.representative;
      const avatarUrl =
        rep && rep.thumb_hash
          ? authedSrc(`/api/v1/faces/crop/${rep.thumb_hash}/${rep.face_index}`)
          : "";
      item.innerHTML =
        (avatarUrl ? `<img class="iph-tag-avatar" src="${avatarUrl}" alt="">` : "") +
        `<span class="iph-tag-name">${esc(name)}</span>` +
        `<span class="iph-tag-count">${c.photo_count}</span>`;
      item.onclick = () => _iphTagPerson(thumbHash, c.cluster_id);
      list.appendChild(item);
    }
    if (list.children.length === 0) {
      list.innerHTML = '<div class="iph-tag-empty">No matches</div>';
    }
  }

  search.addEventListener("input", () => renderList(search.value));
  search.addEventListener("keydown", (ev) => ev.stopPropagation());
  picker.appendChild(list);
  renderList("");

  document.body.appendChild(picker);
  const x = e.clientX || e.pageX || 200;
  const y = e.clientY || e.pageY || 200;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const pw = 200;
  const ph = Math.min(260, vh - 40);
  picker.style.left = Math.min(x, vw - pw - 8) + "px";
  picker.style.top = Math.min(y, vh - ph - 8) + "px";

  /** @param {MouseEvent} ev */
  const closer = (ev) => {
    if (!picker.contains(/** @type {Node} */ (ev.target))) {
      picker.remove();
      document.removeEventListener("click", closer, true);
      document.removeEventListener("keydown", escHandler, true);
    }
  };
  /** @param {KeyboardEvent} ev */
  const escHandler = (ev) => {
    if (ev.key === "Escape") {
      picker.remove();
      document.removeEventListener("click", closer, true);
      document.removeEventListener("keydown", escHandler, true);
    }
  };
  setTimeout(() => {
    document.addEventListener("click", closer, true);
    document.addEventListener("keydown", escHandler, true);
  }, 10);
  search.focus();
}

/**
 * POST a person tag to /api/faces/tag, dismiss the picker, refresh
 * chips + lightbox.
 *
 * @param {string} thumbHash
 * @param {number} clusterId
 */
export async function _iphTagPerson(thumbHash, clusterId) {
  /** @type {any} */
  const win = window;
  document.getElementById("iph-tag-picker")?.remove();
  try {
    await apiFetch("/api/v1/faces/tag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path_hash: thumbHash, cluster_id: clusterId }),
    });
    // No toast: _iphLoadFaces + updateLightboxFaces re-render the panel and
    // overlay with the assigned name, so the result is visible on the same
    // surface. (Toast-noise audit item 13.)
    _iphLoadFaces(thumbHash);
    const idx = /** @type {number} */ (win.lightboxIdx);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (typeof idx === "number" && idx >= 0 && idx < items.length) {
      win.updateLightboxFaces?.(items[idx]);
    }
  } catch (err) {
    toastError("tag this person", err);
  }
}

/**
 * PUT a new name on the smart_person album that owns this cluster.
 *
 * @param {MouseEvent} e
 * @param {number} clusterId
 */
export async function iphRenameFace(e, clusterId) {
  e.stopPropagation();
  /** @type {any} */
  const win = window;
  const current = win.personDisplayName?.(clusterId) || "";
  const name = await appPrompt("Rename person", {
    placeholder: "Name",
    value: current,
    okLabel: "Save",
  });
  if (name == null || name === current) return;
  const albumId = win.getPersonAlbumId?.(clusterId);
  if (!albumId) return;
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    await win.loadAlbumList?.();
    const idx = /** @type {number} */ (win.lightboxIdx);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (typeof idx === "number" && idx >= 0 && idx < items.length) {
      win.updateLightboxFaces?.(items[idx]);
    }
  } catch (err) {
    toastError("rename this person", err);
  }
}

/**
 * Copy `path` to the clipboard. Briefly flashes "Copied!" on the
 * triggering element. Falls back to a toast on failure.
 *
 * @param {HTMLElement} el
 * @param {string} path
 */
export function _iphCopyPath(el, path) {
  navigator.clipboard
    .writeText(path)
    .then(() => {
      const orig = el.textContent;
      el.textContent = "Copied!";
      el.classList.add("iph-copied");
      setTimeout(() => {
        el.textContent = orig;
        el.classList.remove("iph-copied");
      }, 1200);
    })
    .catch((e) => {
      toastError("copy the path", e);
    });
}

/**
 * Format an EXIF dict into a single camera label — "Make Model"
 * with redundant make stripped from the model.
 *
 * @param {{ make?: string, model?: string } | null | undefined} exif
 * @returns {string | null}
 */
export function _iphCamera(exif) {
  if (!exif) return null;
  /** @type {string[]} */
  const parts = [];
  if (exif.make) parts.push(exif.make);
  if (exif.model) {
    const model = exif.model.replace(exif.make || "", "").trim();
    parts.push(model);
  }
  return parts.length > 0 ? parts.join(" ") : null;
}

/**
 * Format the EXIF width × height as a single string.
 *
 * @param {{ width?: number, height?: number } | null | undefined} exif
 * @returns {string | null}
 */
export function _iphSize(exif) {
  if (!exif) return null;
  if (exif.width && exif.height) return exif.width + " × " + exif.height;
  return null;
}
