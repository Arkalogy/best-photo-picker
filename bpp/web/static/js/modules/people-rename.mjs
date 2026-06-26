// @ts-check
/**
 * Person rename + name-autocomplete flows — inline rename in the nav,
 * the Faces grid, and the lightbox, plus the shared name-suggestion
 * dropdown. Split out of people.mjs when the LOC gate caught it over
 * the 500-line cap (2026-06-12); people.mjs re-exports everything
 * here, so callers and the window bridge are unchanged.
 */

import { apiFetch } from "./api-client.mjs";
import { esc } from "./text-format.mjs";
import { loadAlbumList, renderAlbumNav } from "./albums.mjs";
import { refreshSmartAlbums, renderFaceGallery } from "./faces.mjs";
import { toast, toastError } from "./toast.mjs";
import { getPersonAlbumId, getPersonName, personDisplayName } from "./people.mjs";
import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { doMerge } from "./people-merge.mjs";
import { personLabelHTML, showPeopleView } from "./people-view.mjs";

/**
 * Real (user-given) names of all other people — feeds the rename input's
 * autocomplete and the merge-on-match flow. "Person N" placeholders are
 * not names, so they're excluded.
 */
export function personNameSuggestions(excludeClusterId) {
  const names = [];
  for (const a of state.albumList || []) {
    if (a.album_type !== "smart_person" || !a.name) continue;
    if (/^Person \d+$/.test(a.name)) continue;
    if (a.rule && a.rule.cluster_id === excludeClusterId) continue;
    names.push(a.name);
  }
  return [...new Set(names)].sort((x, y) => x.localeCompare(y));
}

/**
 * Wire a rename input to a styled suggestion dropdown of existing person
 * names, so typing "Ri" suggests "Rita". Custom (not <datalist>) because
 * the native picker is unstylable and renders illegibly in the Tauri
 * webview. Picking a suggestion fills the input and commits via the
 * caller's blur handler — which routes a duplicate name into the
 * merge-confirmation flow.
 *
 * MUST be attached BEFORE the caller's own keydown listener so Enter can
 * fill the highlighted name before the caller's Enter→blur commit runs,
 * and Escape-with-dropdown-open can close just the dropdown
 * (stopImmediatePropagation) without cancelling the rename.
 */
export function attachPersonNameAutocomplete(input, clusterId) {
  const names = personNameSuggestions(clusterId);
  if (!names.length) return;
  /** @type {HTMLElement|null} */ let box = null;
  let items = [];
  let hi = -1; // highlighted index (keyboard)

  const close = () => { if (box) box.remove(); box = null; items = []; hi = -1; };
  const render = () => {
    const q = input.value.trim().toLowerCase();
    items = (q ? names.filter(n => n.toLowerCase().includes(q)) : names).slice(0, 8);
    if (!items.length) { close(); return; }
    if (!box) {
      box = document.createElement("div");
      box.className = "person-suggest";
      box.addEventListener("mousedown", (e) => {
        const it = /** @type {HTMLElement | null} */ (
          /** @type {HTMLElement} */ (e.target).closest(".person-suggest-item")
        );
        if (!it) return;
        // preventDefault keeps focus on the input so there's exactly ONE
        // blur → one commit, after we fill the value.
        e.preventDefault();
        input.value = items[Number(it.dataset.idx)];
        close();
        input.blur();
      });
      document.body.appendChild(box);
    }
    const r = input.getBoundingClientRect();
    box.style.left = `${r.left}px`;
    box.style.top = `${r.bottom + 2}px`;
    box.style.minWidth = `${Math.max(r.width, 140)}px`;
    box.innerHTML = items
      .map((n, i) => `<div class="person-suggest-item${i === hi ? " active" : ""}" data-idx="${i}">${esc(n)}</div>`)
      .join("");
  };

  input.addEventListener("input", () => { hi = -1; render(); });
  input.addEventListener("focus", render);
  input.addEventListener("keydown", (e) => {
    if (!box) return;
    if (e.key === "ArrowDown") { hi = (hi + 1) % items.length; render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { hi = (hi - 1 + items.length) % items.length; render(); e.preventDefault(); }
    else if (e.key === "Enter" && hi >= 0) { input.value = items[hi]; close(); }
    else if (e.key === "Escape") { close(); e.stopImmediatePropagation(); }
  });
  // Delay so a suggestion mousedown lands before the dropdown goes away.
  input.addEventListener("blur", () => setTimeout(close, 150));
}

/**
 * Rename a person's album. Returns true only when the rename actually
 * persisted, so callers can revert their optimistic label on failure
 * (no-op early-returns and the merge branch return false — the merge path
 * re-renders the view itself).
 * @returns {Promise<boolean>}
 */
export async function renamePerson(clusterId, newName) {
  const albumId = getPersonAlbumId(clusterId);
  if (!albumId) return false;
  const trimmed = newName.trim();
  if (!trimmed) return false;
  // Renaming onto an existing person's name = the user is saying "this is
  // the same person" — offer to merge instead of dead-ending on a warning.
  const dupe = state.albumList.find(a =>
    a.album_type === "smart_person" && a.id !== albumId &&
    a.name.toLowerCase() === trimmed.toLowerCase()
  );
  if (dupe) {
    const dupeClusterId = dupe.rule && dupe.rule.cluster_id;
    if (dupeClusterId == null) {
      toast(`Another person is already named "${dupe.name}"`, "warning");
      return false;
    }
    const ok = await appConfirm(
      `"${dupe.name}" already exists. Merge this person into ${dupe.name}?`
    );
    if (ok) await doMerge(dupeClusterId, [clusterId]);
    return false; // merge (or cancel) re-renders the view; don't keep the inline label
  }
  try {
    await apiFetch(`/api/v1/albums/${albumId}`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: trimmed}),
    });
    await loadAlbumList();
    showPeopleView();
    return true;
  } catch (e) {
    // Without this, a failed rename falls through to the global error
    // boundary's generic "Something went wrong" toast, which names neither
    // the action nor the reason. apiFetch's error .message is already the
    // server's reason (body.error) or "HTTP <status>".
    toastError(`rename to "${trimmed}"`, e);
    return false;
  }
}

export function startNavFaceRename(clusterId, el) {
  const current = el.textContent.trim();
  const orig = current;
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.placeholder = "Add name";
  input.className = "inline-rename-input nav-rename";
  attachPersonNameAutocomplete(input, clusterId);
  const commit = async () => {
    const val = input.value.trim();
    if (val && val !== orig) {
      // On success renamePerson re-renders the people view (el is replaced);
      // on failure restore the original label so it doesn't stay edited.
      const ok = await renamePerson(clusterId, val);
      if (!ok) el.textContent = orig;
    } else {
      el.textContent = orig;
    }
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") { el.textContent = orig; }
  });
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("blur", commit);
  el.textContent = "";
  el.appendChild(input);
  input.focus();
  input.select();
}

export function startPersonRename(clusterId, el) {
  // Resolve the label element. When dispatched via data-action the dispatcher
  // sets `this` to the clicked element; direct callers (ctx menu, lightbox)
  // pass an explicit element. The grid card used to pass a literal
  // "this.parentElement" STRING (the dispatcher doesn't eval that), so `el`
  // was a string and `el.innerHTML = ""` threw — the rename failed every time.
  const target = el instanceof Element ? el : this instanceof Element ? this : null;
  if (!target) return;
  const current = personDisplayName(clusterId) || "";
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.placeholder = "Add name";
  input.className = "inline-rename-input";
  attachPersonNameAutocomplete(input, clusterId);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { input.blur(); }
    if (e.key === "Escape") { target.innerHTML = personLabelHTML(clusterId); }
  });
  input.addEventListener("blur", async () => {
    if (input.value.trim()) {
      const ok = await renamePerson(clusterId, input.value);
      if (!ok) target.innerHTML = personLabelHTML(clusterId);
    } else {
      target.innerHTML = personLabelHTML(clusterId);
    }
  });
  target.innerHTML = "";
  target.appendChild(input);
  input.focus();
  input.select();
}

export function startPersonRenameLightbox(clusterId) {
  // Find the face chip in the lightbox by cluster ID
  const chip = document.querySelector(`.lb-face-chip[data-cluster-id="${clusterId}"]`);
  const nameSpan = chip && chip.querySelector(".lb-face-name");
  if (!nameSpan) {
    // Lightbox not open or chip not found — fall back to people grid label
    const label = document.getElementById(`person-label-${clusterId}`);
    if (label) { startPersonRename(clusterId, label); return; }
    return;
  }

  const current = personDisplayName(clusterId) || "";
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.placeholder = "Add name";
  input.className = "inline-rename-input";
  input.style.cssText = "width:80px;font-size:11px;padding:1px 4px;";
  attachPersonNameAutocomplete(input, clusterId);
  const origText = nameSpan.textContent;

  input.addEventListener("keydown", (e) => {
    e.stopPropagation(); // Prevent lightbox key handlers
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") { nameSpan.textContent = origText; }
  });
  input.addEventListener("blur", async () => {
    if (input.parentNode !== nameSpan) return;
    const val = input.value.trim();
    if (!val) {
      nameSpan.textContent = origText;
      return;
    }
    // showPeopleView re-renders the grid, not this lightbox chip — so set
    // the chip label from the result: new name on success, original on fail.
    const ok = await renamePerson(clusterId, val);
    nameSpan.textContent = ok ? val : origText;
  });

  nameSpan.textContent = "";
  nameSpan.appendChild(input);
  input.focus();
  input.select();
}
