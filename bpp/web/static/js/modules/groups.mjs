// @ts-check
/**
 * Groups: people who appear together in photos. Server returns
 * detected co-occurrence sets via /api/groups; this module owns the
 * list, the Groups view rendering, the inline rename flow, and the
 * navigateToGroups / navigateToGroupAlbum entry points.
 *
 * Bridged onto window for inline onclick + classic-side callers
 * (app.js bootstrap, albums.js sidebar tree, core.js navigateTo
 * dispatcher).
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toastError } from "./toast.mjs";

/**
 * @typedef {Object} GroupMember
 * @property {string} name
 * @property {string | null} [thumb_hash]
 * @property {number} [face_index]
 */

/**
 * @typedef {Object} Group
 * @property {number} [album_id]
 * @property {string} [album_name]
 * @property {GroupMember[]} member_info
 * @property {number} photo_count
 */

/** @type {Group[]} */
let faceGroups = [];

export function _getFaceGroups() {
  return faceGroups;
}

/** @param {Group[]} g */
export function _setFaceGroups(g) {
  faceGroups = g;
}

/**
 * Refresh `faceGroups` from /api/v1/groups.
 *
 * Protection B: routed through wrapSectionLoader so a /api/v1/groups
 * failure surfaces as a retry pill in the Groups sidebar section
 * instead of silently leaving the list empty.
 */
export async function loadGroups() {
  const { wrapSectionLoader } = await import("./sidebar-safety.mjs");
  return wrapSectionLoader("groups", _loadGroupsInner);
}

async function _loadGroupsInner() {
  const data = await apiFetch("/api/v1/groups");
  faceGroups = rankGroupsNamedFirst(data.groups || []);
}

/** Mirror of the backend's auto-name token (smart_album_groups.py). */
const AUTO_NAME_TOKEN = /^Person \d+$/;

/** True when a group has ≥1 member the user has actually named (not a
 *  "Person N" auto-cluster). The user's complaint was that all-unnamed
 *  groups are meaningless noise. */
export function groupHasNamedMember(group) {
  return (group.member_info || []).some((m) => m.name && !AUTO_NAME_TOKEN.test(m.name));
}

/** Stable rank: groups with a named member first, the rest below in their
 *  original order. Pure — returns a new array. */
export function rankGroupsNamedFirst(groups) {
  return [...(groups || [])].sort(
    (a, b) => (groupHasNamedMember(b) ? 1 : 0) - (groupHasNamedMember(a) ? 1 : 0),
  );
}

/**
 * Display name for a group, distinguishing user-renamed albums from
 * the auto-generated "A & B" default. Returns null when the album
 * still has its default auto name — including a STALE one ("Person 2 &
 * Person 5" baked before a member was renamed): if every " & "-token is
 * "Person N" or a current member name, the name carries no user input,
 * so the view falls back to the live member names.
 *
 * @param {Group} group
 * @returns {string | null}
 */
export function groupDisplayName(group) {
  if (!group.album_name) return null;
  const memberNames = group.member_info.map((m) => m.name);
  if (group.album_name === memberNames.join(" & ")) return null;
  const tokens = group.album_name.split(" & ");
  if (
    tokens.length > 1 &&
    tokens.every((t) => AUTO_NAME_TOKEN.test(t) || memberNames.includes(t))
  ) {
    return null;
  }
  return group.album_name;
}

/** @param {Group} group */
export function groupDefaultName(group) {
  return group.member_info.map((m) => m.name).join(" & ");
}

/**
 * PUT a new album name to the server, then refresh the album list +
 * groups + Groups view.
 *
 * @param {Group} group
 * @param {string} newName
 */
export async function renameGroup(group, newName) {
  if (!group.album_id) return;
  const trimmed = newName.trim();
  if (!trimmed) return;
  try {
    await apiFetch(`/api/v1/albums/${group.album_id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    /** @type {any} */
    const win = window;
    await win.loadAlbumList?.();
    await loadGroups();
    showGroupsView();
  } catch (e) {
    toastError("rename this group", e);
  }
}

/**
 * Replace the name label of a group card with an inline edit input.
 * Enter / blur commits, Esc cancels.
 *
 * @param {Group} group
 * @param {HTMLElement} el
 */
export function startGroupRename(group, el) {
  // Inline rename runs from an inline onclick handler that uses the
  // implicit `event` global; preserve that behavior.
  /** @type {any} */ (window).event?.stopPropagation?.();
  const current = groupDisplayName(group) || "";
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.placeholder = groupDefaultName(group);
  input.className = "inline-rename-input";
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") showGroupsView();
  });
  input.addEventListener("blur", () => {
    if (input.value.trim()) {
      renameGroup(group, input.value);
    } else {
      showGroupsView();
    }
  });
  el.innerHTML = "";
  el.appendChild(input);
  input.focus();
  input.select();
}

/**
 * Render the stacked-avatar circle for a group card — up to 3 face
 * thumbs + an "+N" overflow chip when more members exist.
 *
 * @param {Group} group
 * @returns {string}
 */
export function groupAvatarsHTML(group) {
  /** @type {any} */
  const win = window;
  const ICONS = win.ICONS || {};
  const members = group.member_info || [];
  const shown = members.slice(0, 3);
  const avatars = shown
    .map((m, i) => {
      const src = m.thumb_hash
        ? authedSrc(`/api/v1/faces/crop/${esc(m.thumb_hash)}/${m.face_index}`)
        : "";
      return src
        ? `<img class="group-stack-img" src="${escapeAttr(src)}" loading="lazy" style="z-index:${10 - i}" title="${escapeAttr(m.name)}">`
        : `<div class="group-stack-placeholder" style="z-index:${10 - i}">${ICONS.people || ""}</div>`;
    })
    .join("");
  const extra =
    members.length > 3
      ? `<span class="group-stack-extra">+${members.length - 3}</span>`
      : "";
  return `<div class="group-stack group-stack-${shown.length}">${avatars}${extra}</div>`;
}

/** Render the Groups view container — empty state or one card per group. */
export function showGroupsView() {
  /** @type {any} */
  const win = window;
  win.currentAlbumId = null;

  const content = document.querySelector(".content");
  let view = /** @type {HTMLElement | null} */ (document.getElementById("groups-view"));
  if (!view) {
    view = document.createElement("div");
    view.id = "groups-view";
    view.className = "people-grid";
    content?.appendChild(view);
  }
  view.classList.remove("hidden");
  win.show?.("toolbar");
  win.show?.("status-bar");

  const ICONS = win.ICONS || {};

  if (faceGroups.length === 0) {
    const msg = !win.faceRecognitionAvailable
      ? `Face recognition is not installed.<br><code class="install-hint">pip install bppicker[faces]</code>`
      : "Groups are detected automatically when multiple people appear together in photos. Import and analyze photos with face detection first.";
    view.innerHTML = `<div class="empty-state people-empty">
      <div class="icon">${ICONS.group || ""}</div>
      <div class="title">No Groups Found</div>
      <div class="desc">${msg}</div>
    </div>`;
    win.updateToolbarTitle?.("Groups", "No groups");
    const summary = document.getElementById("status-summary");
    if (summary) summary.textContent = "No groups";
    return;
  }

  const cards = faceGroups
    .map((g, idx) => {
      const name = groupDisplayName(g) || groupDefaultName(g);
      // The member-name default is the group's real label, not an
      // unnamed placeholder — style it like names on the Faces page.
      const nameCls = "person-name";
      return `<div class="person-card group-card" data-group-idx="${idx}"
      data-action="navigateToGroupAlbum" data-arg0="${idx}">
      <div class="group-avatar-area">
        ${groupAvatarsHTML(g)}
      </div>
      <div class="person-label" id="group-label-${idx}">
        <div class="${nameCls}" title="${escapeAttr(name)}" data-stop-propagation="true" data-action="startGroupRename" data-arg0="_getFaceGroups()[${idx}]" data-arg1="this.parentElement">${esc(name)}</div>
        <div class="person-count">${g.photo_count} photo${g.photo_count === 1 ? "" : "s"}</div>
      </div>
    </div>`;
    })
    .join("");

  view.innerHTML = cards;
  _renderGroupsMergeNudge(view); // async, fire-and-forget

  const subtitle = `${faceGroups.length} group${faceGroups.length === 1 ? "" : "s"}`;
  const summary = document.getElementById("status-summary");
  if (summary) summary.textContent = subtitle;
  win.updateToolbarTitle?.("Groups", subtitle);
}

/**
 * Merge nudge: wrong/low group counts come from one person being split
 * across duplicate clusters, so groups they belong to splinter. When the
 * face-pair-review wizard has same-person candidates, surface a banner at
 * the top of Groups (where the low counts are felt) that explains the link
 * in plain words and routes into the existing merge wizard. Merging there
 * consolidates the people → the splintered groups re-fuse with correct
 * counts. Reuses people-pair-review (lazy-imported to avoid coupling the
 * Groups load path to the faces chain).
 * @param {HTMLElement} view  the #groups-view container
 */
export async function _renderGroupsMergeNudge(view) {
  const { refreshAmbiguousPairCount, getAmbiguousPairCount } = await import(
    "./people-pair-review.mjs"
  );
  await refreshAmbiguousPairCount();
  const n = getAmbiguousPairCount() || 0;
  view.querySelector(".groups-merge-nudge")?.remove();
  if (n <= 0) return;
  const banner = document.createElement("div");
  banner.className = "groups-merge-nudge";
  banner.innerHTML =
    `<span>Some people may be split into duplicates, which makes these group counts look low. ` +
    `<strong>${n}</strong> pair${n === 1 ? "" : "s"} look like the same person.</span>` +
    `<button class="preset-btn save" data-action="startFacePairReview">Review merges</button>`;
  view.insertBefore(banner, view.firstChild);
}

/**
 * Navigate to a group's album. If the album doesn't exist yet, refresh
 * smart albums first to create it.
 *
 * @param {number} idx
 */
export async function navigateToGroupAlbum(idx) {
  const group = faceGroups[idx];
  if (!group) return;

  /** @type {any} */
  const win = window;
  if (group.album_id) {
    const albumList = /** @type {any[]} */ (win.albumList || []);
    const album = albumList.find((a) => a.id === group.album_id);
    if (album) {
      win.navigateTo?.("album", group.album_id);
      return;
    }
  }

  await win.refreshSmartAlbums?.();
  await loadGroups();
  const updated = faceGroups[idx];
  if (updated && updated.album_id) {
    win.navigateTo?.("album", updated.album_id);
  }
}

export function navigateToGroups() {
  /** @type {any} */ (window).navigateTo?.("groups");
}
