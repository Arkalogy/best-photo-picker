// @ts-check
/**
 * Moments-as-stacks: the Moments album collapses each burst to ONE cover
 * card (the keeper) with a count badge, so it reads as a prune queue —
 * unmistakably different from the gallery (≈one card per burst, not every
 * photo). Clicking a stack opens the existing compare/prune overlay on
 * that burst (keeper vs the others). Layout is the ordinary uniform
 * virtualized grid — no bespoke layout to break.
 *
 * `buildMomentStacks` is pure (tested directly); the render path feeds its
 * output to the same vgrid every album uses.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import { computeMomentKeepers, momentScore } from "./moments-view.mjs";
import { openCompareWithSibling } from "./compare-sibling.mjs";

/**
 * Collapse a flat photo list into one cover per Moment.
 *
 * Each returned cover is a SHALLOW CLONE of the keeper (never the shared
 * photo object — mutating that would leak the badge into other albums)
 * carrying `_momentCount` (burst size) and `_momentSiblings` (the
 * non-keeper photos, for the compare overlay). Singletons / non-Moment
 * photos are dropped. Covers are ordered by the burst's earliest date so
 * the queue reads chronologically.
 *
 * @param {any[]} photos
 * @returns {any[]} cover photos (clones) with _momentCount + _momentSiblings
 */
export function buildMomentStacks(photos) {
  const keepers = computeMomentKeepers(photos || []);
  /** @type {Map<number, {members: any[], earliest: string}>} */
  const groups = new Map();
  for (const p of photos || []) {
    if (p.deleted_at) continue; // trashed shots don't count toward a burst
    const mid = p.moment_cluster_id || 0;
    if (!mid || (p.moment_size || 1) < 2) continue;
    let g = groups.get(mid);
    if (!g) {
      g = { members: [], earliest: p.date || "" };
      groups.set(mid, g);
    }
    g.members.push(p);
    if (!g.earliest || (p.date || "") < g.earliest) g.earliest = p.date || "";
  }

  /** @type {any[]} */
  const covers = [];
  for (const [, g] of groups) {
    // A burst needs >= 2 LIVE (non-trashed) members to still be a burst —
    // trash one of a pair and the stack vanishes.
    if (g.members.length < 2) continue;
    // Keeper = the precomputed keeper if present in the loaded members,
    // else the highest-scoring loaded member (covers refine as background
    // pages stream in).
    let keeper = g.members.find((p) => keepers.has(p.filepath));
    if (!keeper) {
      keeper = g.members.reduce((best, p) => (momentScore(p) > momentScore(best) ? p : best));
    }
    const siblings = g.members.filter((p) => p.filepath !== keeper.filepath);
    covers.push({
      ...keeper,
      _momentCount: g.members.length,
      _momentSiblings: siblings,
      _momentEarliest: g.earliest,
    });
  }
  covers.sort((a, b) => (a._momentEarliest || "").localeCompare(b._momentEarliest || ""));
  return covers;
}

/**
 * Open the compare/prune overlay on a stack's burst, focused on sibling
 * `idx` (the keeper is always the left/parent side). No-op if the burst's
 * siblings haven't finished loading yet.
 * @param {any} cover  a cover produced by buildMomentStacks
 * @param {number} [idx]  which sibling to show on the right (default 0)
 */
export function openMomentStack(cover, idx = 0) {
  const siblings = (cover && cover._momentSiblings) || [];
  if (!siblings.length) return;
  openCompareWithSibling(cover, siblings, Math.max(0, Math.min(idx, siblings.length - 1)));
}

// ── Burst flyout: hover/click a stack → expand its shots in place ──

/** @type {HTMLElement | null} */
let _flyout = null;
/** @type {any} */
let _flyoutCover = null;
/** Crowned keeper for the open flyout (the shot "Trash the rest" spares).
 *  Defaults to the computed keeper (cover.filepath); the star control
 *  re-crowns it. */
let _flyoutKeeperPath = null;
/** Pinned (survives the compare round-trip + hover-out) once a thumb is clicked. */
let _flyoutPinned = false;
/** @type {ReturnType<typeof setTimeout> | null} */
let _showTimer = null;
/** @type {ReturnType<typeof setTimeout> | null} */
let _hideTimer = null;
const _HOVER_OPEN_MS = 200; // wait before opening so scrolling past doesn't flash it
const _HOVER_CLOSE_MS = 220; // grace to cross the gap from card → strip

function _ensureFlyout() {
  if (_flyout) {
    // Re-attach if something detached it (e.g. a full view re-render).
    if (!_flyout.isConnected) document.body.appendChild(_flyout);
    return _flyout;
  }
  const fly = document.createElement("div");
  fly.id = "moment-burst-flyout";
  fly.className = "moment-burst-flyout hidden";
  fly.addEventListener("click", (e) => {
    const t = /** @type {HTMLElement} */ (e.target);
    if (!_flyoutCover) return;
    // Crown star (option B): re-designate the keeper without opening compare.
    const crown = t.closest?.(".mbt-crown");
    if (crown) {
      e.stopPropagation();
      _crownMomentKeeper(/** @type {HTMLElement} */ (crown).dataset.fp || "");
      return;
    }
    // "Trash the rest" footer button.
    if (t.closest?.(".mbt-prune")) {
      e.stopPropagation();
      _pruneMomentBurst();
      return;
    }
    // Thumb body → compare (unchanged). data-sib-idx: -1 = keeper, else the
    // clicked photo's index within _momentSiblings.
    const thumb = t.closest?.(".moment-burst-thumb");
    if (!thumb) return;
    const sib = Number(/** @type {HTMLElement} */ (thumb).dataset.sibIdx);
    // Pin so the strip survives the compare overlay opening over it.
    _flyoutPinned = true;
    openMomentStack(_flyoutCover, sib < 0 ? 0 : sib);
  });
  document.body.appendChild(fly);
  _flyout = fly;
  return fly;
}

/**
 * Pure split for "keep one, trash the rest" over a burst. The burst is the
 * cover (computed keeper) plus its loaded siblings; everything except the
 * crowned keeper is trashed. Unit-tested.
 * @param {any} cover  cover with _momentSiblings
 * @param {string} [keeperPath]  crowned keeper; defaults to the computed keeper
 * @returns {{keep: string, trash: string[]}}
 */
export function bulkPrunePlan(cover, keeperPath) {
  const members = [cover, ...((cover && cover._momentSiblings) || [])].filter(Boolean);
  const keep = keeperPath || (cover && cover.filepath) || "";
  const trash = members.map((p) => p.filepath).filter((fp) => fp && fp !== keep);
  return { keep, trash };
}

/**
 * Build the flyout's inner HTML: one thumb per burst member (keeper first),
 * each with a score, a crown star (filled on the crowned keeper), and a
 * footer "Trash the other N" button. Pure-ish (reads only its args).
 * @param {any} cover
 * @param {string} keeperPath
 */
function _flyoutInnerHTML(cover, keeperPath) {
  const all = [cover, ...(cover._momentSiblings || [])]; // computed keeper first
  const thumbs = all
    .map((p, i) => {
      const isKeeper = p.filepath === keeperPath;
      const score = Math.round((p.aggregate_score || 0) * 100);
      const sibIdx = i === 0 ? -1 : i - 1; // -1 = the cover (computed keeper)
      const fp = escapeAttr(p.filepath || "");
      return (
        `<div class="moment-burst-thumb${isKeeper ? " is-keeper" : ""}" data-sib-idx="${sibIdx}">` +
        `<img src="${escapeAttr(authedSrc("/thumb/" + p.thumb_hash))}" loading="lazy" alt="">` +
        `<span class="mbt-score">${score}%</span>` +
        `<button class="mbt-crown${isKeeper ? " active" : ""}" data-fp="${fp}" ` +
        `title="Keep this one" aria-label="Keep this shot">&#9733;</button>` +
        (isKeeper ? `<span class="mbt-keeper-tag">keeper</span>` : "") +
        `</div>`
      );
    })
    .join("");
  const trashN = bulkPrunePlan(cover, keeperPath).trash.length;
  const footer =
    `<div class="moment-burst-actions">` +
    `<button class="mbt-prune"${trashN ? "" : " disabled"}>Trash the other ${trashN}</button>` +
    `</div>`;
  return `<div class="moment-burst-strip">${thumbs}</div>${footer}`;
}

/**
 * Re-crown the keeper (star click): update state + re-render in place. No
 * reposition — the flyout is already open.
 * @param {string} fp
 */
export function _crownMomentKeeper(fp) {
  if (!fp || !_flyoutCover || !_flyout) return;
  _flyoutKeeperPath = fp;
  _flyout.innerHTML = _flyoutInnerHTML(_flyoutCover, _flyoutKeeperPath);
}

/**
 * Populate + position the flyout for a stack cover. Anchored below the
 * card (flips above when it would overflow); position:fixed so it never
 * reflows the grid.
 * @param {any} cover  cover with _momentSiblings
 * @param {HTMLElement} card  the stack card to anchor to
 */
function _showFlyout(cover, card) {
  if (!cover || !cover._momentSiblings) return;
  _flyoutCover = cover;
  // Default the crowned keeper to the computed keeper (the cover).
  _flyoutKeeperPath = cover.filepath;
  const fly = _ensureFlyout();
  fly.innerHTML = _flyoutInnerHTML(cover, _flyoutKeeperPath);
  // Overlap the card's bottom edge slightly (negative gap) so there's no
  // dead zone to cross between the card and the strip — moving down from
  // the stack lands directly on the strip.
  fly.classList.remove("hidden");
  const r = card.getBoundingClientRect();
  const fr = fly.getBoundingClientRect();
  let left = Math.min(r.left, window.innerWidth - 8 - fr.width);
  left = Math.max(8, left);
  let top = r.bottom - 4;
  if (top + fr.height > window.innerHeight - 8) top = r.top - fr.height + 4; // flip above
  top = Math.max(8, top);
  fly.style.left = `${left}px`;
  fly.style.top = `${top}px`;
}

function _hideFlyout() {
  if (_flyout) _flyout.classList.add("hidden");
  _flyoutCover = null;
  _flyoutKeeperPath = null;
  _flyoutPinned = false;
}

/**
 * Mark a batch of photos trashed/untrashed in the in-memory window.photos
 * and live-update the grid (the Moments stacks re-collapse via
 * buildMomentStacks, which skips deleted_at). No-op when there's no grid.
 * @param {string[]} filepaths
 * @param {boolean} deleted
 */
function _setPhotosDeletedInMemory(filepaths, deleted) {
  /** @type {any} */
  const win = window;
  const set = new Set(filepaths);
  const stamp = deleted ? new Date().toISOString() : null;
  for (const p of win.photos || []) {
    if (set.has(p.filepath)) p.deleted_at = stamp;
  }
  win.renderGrid?.({ keepScroll: true });
}

/**
 * "Trash the other N": confirm, batch-trash every burst member except the
 * crowned keeper, live-update the grid, and offer a 20s recoverable Undo
 * (congruent with the per-sibling prune). Never auto-deletes — appConfirm
 * gates it (project convention). Exposed for the flyout click handler + tests.
 */
export async function _pruneMomentBurst() {
  if (!_flyoutCover) return;
  const { keep, trash } = bulkPrunePlan(_flyoutCover, _flyoutKeeperPath);
  if (!trash.length) return;
  const keeperName = (keep.split("/").pop() || "the best shot").trim();
  const ok = await appConfirm(
    `Trash ${trash.length} shot${trash.length === 1 ? "" : "s"}, keep "${keeperName}"?`,
    "They go to trash (recoverable). The kept shot stays.",
  );
  if (!ok) return;
  try {
    await apiFetch("/api/v1/photos/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: trash }),
    });
  } catch (e) {
    toastError("trash those shots", e);
    return;
  }
  _setPhotosDeletedInMemory(trash, true);
  _hideFlyout(); // the burst is now just the keeper
  toast(`Trashed ${trash.length}, kept "${keeperName}"`, undefined, {
    duration: 20000,
    action: { label: "Undo", fn: () => _undoMomentPrune(trash) },
  });
}

/**
 * Restore a just-trashed burst: un-delete server-side + in-memory.
 * @param {string[]} filepaths
 */
async function _undoMomentPrune(filepaths) {
  try {
    await apiFetch("/api/v1/photos/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths }),
    });
  } catch (e) {
    toastError("restore those shots", e);
    return;
  }
  _setPhotosDeletedInMemory(filepaths, false);
  toast("Restored");
}

/** Cancel any pending open/close timers. */
function _clearTimers() {
  if (_showTimer) clearTimeout(_showTimer);
  if (_hideTimer) clearTimeout(_hideTimer);
  _showTimer = null;
  _hideTimer = null;
}

/**
 * Expand the burst at grid index `idx` (the click path) — immediate, no
 * hover delay, and pinned so it persists. Finds the card, looks up its
 * cover in currentGridItems, shows the flyout.
 * @param {number} idx
 */
export function expandMomentStack(idx) {
  /** @type {any} */
  const win = window;
  const cover = (win.currentGridItems || [])[idx];
  const card = /** @type {HTMLElement | null} */ (
    document.querySelector(`#photo-grid .card[data-idx="${idx}"]`)
  );
  if (cover && card) {
    _clearTimers();
    _flyoutPinned = true;
    _showFlyout(cover, card);
  }
}

let _inited = false;

/**
 * Wire the burst-flyout hover behavior: delayed open over a stack, grace
 * close when moving off the card+strip, and "stay open while compare is
 * visible" so a thumb→compare round-trip lands back on the same strip.
 *
 * One delegated `mouseover` listener on `document` (idempotent — survives
 * a grid re-render, and a repeat call is a no-op). No global click
 * listener: pin is released by compare-overlay visibility, not by clicks.
 */
export function initMomentBurstFlyout() {
  if (_inited) return;
  _inited = true;

  document.addEventListener("mouseover", (e) => {
    const t = /** @type {HTMLElement} */ (e.target);
    const card = /** @type {HTMLElement | null} */ (t.closest?.(".moment-stack"));

    // Over a stack card in the grid → schedule a delayed open (don't flash
    // the strip while scrolling past).
    if (card && card.closest?.("#photo-grid")) {
      if (_hideTimer) {
        clearTimeout(_hideTimer);
        _hideTimer = null;
      }
      const idx = parseInt(card.dataset.idx || "", 10);
      const cover = /** @type {any} */ (window).currentGridItems?.[idx];
      if (!cover) return;
      if (!_flyout?.classList.contains("hidden") && _flyoutCover === cover) return; // already showing
      if (_showTimer) clearTimeout(_showTimer);
      _showTimer = setTimeout(() => {
        _flyoutPinned = false;
        _showFlyout(cover, card);
      }, _HOVER_OPEN_MS);
      return;
    }

    // Over the strip → cancel any pending close.
    if (t.closest?.("#moment-burst-flyout")) {
      if (_hideTimer) {
        clearTimeout(_hideTimer);
        _hideTimer = null;
      }
      return;
    }

    // Off both. Cancel a pending open; grace-close the open strip.
    if (_showTimer) {
      clearTimeout(_showTimer);
      _showTimer = null;
    }
    if (!_flyout || _flyout.classList.contains("hidden")) return;
    if (_flyoutPinned) {
      const cmp = document.getElementById("compare-overlay");
      if (cmp && cmp.classList.contains("visible")) return; // keep while comparing
      _flyoutPinned = false; // compare closed → allow normal hover-close
    }
    if (!_hideTimer) _hideTimer = setTimeout(_hideFlyout, _HOVER_CLOSE_MS);
  });
}

/** Test-only: tear down flyout state between tests. */
export function _resetMomentFlyout() {
  _clearTimers();
  if (_flyout) {
    _flyout.remove();
    _flyout = null;
  }
  _flyoutCover = null;
  _flyoutKeeperPath = null;
  _flyoutPinned = false;
}
