// @ts-check
/**
 * Face review wizards: single-face review + ambiguous-pair review.
 *
 * Extracted from people.mjs during the v0.1 cleanup. This module
 * owns the two "review unclustered faces" workflows that sit on top
 * of the face_embeddings table:
 *
 * 1. **Single-face review** — walks unreviewed faces one at a time,
 *    user names the person or merges into an existing cluster.
 *    State: _reviewData, _reviewIndex, _reviewMergeTarget.
 *
 * 2. **Ambiguous-pair review** — walks cluster pairs whose centroid
 *    distance is below the merge threshold, user records
 *    same/different/skip verdicts. Teaches the adaptive threshold
 *    via face_feedback (same → merge signal) and blocks future
 *    auto-merges via hard_negatives.
 *    State: _pairReviewData, _pairReviewIndex, _pairReviewStats,
 *    _ambiguousPairCount.
 *
 * Re-exported from people.mjs so the modules-bridge in
 * templates/index.html keeps exposing every data-action handler on
 * window unchanged.
 */

import { apiFetch } from "./api-client.mjs";
import { state } from "./state.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters, refreshSmartAlbums } from "./faces.mjs";
import { toast, toastError } from "./toast.mjs";
import { getPersonAlbumId, personDisplayName } from "./people.mjs";
import {
  _previewArgs,
  manualActionsHTML,
  reviewCompleteHTML,
  reviewPanelHTML,
  reviewSamplesHTML,
  singleAvatarHTML,
  suggestionActionsHTML,
  suggestionAvatarHTML,
} from "./people-review-card.mjs";


// ── Face Review Mode ──

let _reviewData = null;   // { unreviewed: [...], total, reviewed }
let _reviewIndex = 0;     // current position in unreviewed list
let _reviewMergeTarget = null; // { cluster_id, name } if merging into existing person

export async function startFaceReview() {
  try {
    _reviewData = await apiFetch("/api/v1/faces/review");
  } catch (e) {
    toastError("load review data", e);
    return;
  }
  if (!_reviewData.unreviewed || _reviewData.unreviewed.length === 0) {
    toast("All people have been reviewed!");
    return;
  }
  _reviewIndex = 0;
  _showReviewOverlay();
}

/**
 * Capture-phase Esc handler for the review modal. Esc closes the modal —
 * EXCEPT when the name autocomplete dropdown is open, where it closes just
 * the dropdown (so typing a name isn't interrupted). Capture phase +
 * stopImmediatePropagation keeps it from bubbling into the global/lightbox
 * Esc handlers. Module-level (stable reference) so add/remove is idempotent.
 * @param {KeyboardEvent} e
 */
function _reviewOverlayKey(e) {
  if (e.key !== "Escape") return;
  const overlay = document.getElementById("face-review-overlay");
  if (!overlay || !overlay.classList.contains("visible")) return;
  const dropdown = document.getElementById("review-autocomplete");
  if (dropdown && dropdown.innerHTML.trim() !== "") {
    e.stopImmediatePropagation();
    dropdown.innerHTML = "";
    return;
  }
  e.stopImmediatePropagation();
  _closeReviewOverlay();
}

export function _showReviewOverlay() {
  let overlay = document.getElementById("face-review-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "face-review-overlay";
    overlay.className = "modal-overlay";
    document.body.appendChild(overlay);
  }
  overlay.classList.add("visible");
  document.removeEventListener("keydown", _reviewOverlayKey, true); // idempotent
  document.addEventListener("keydown", _reviewOverlayKey, true);
  _renderReviewCard();
}

export function _closeReviewOverlay() {
  const overlay = document.getElementById("face-review-overlay");
  if (overlay) overlay.classList.remove("visible");
  document.removeEventListener("keydown", _reviewOverlayKey, true);
  // Refresh people view to reflect any changes
  loadFaceClusters();
}

export function _renderReviewCard() {
  const overlay = document.getElementById("face-review-overlay");
  if (!overlay || !_reviewData) return;

  const { unreviewed, total, reviewed } = _reviewData;
  if (_reviewIndex >= unreviewed.length) {
    overlay.innerHTML = reviewCompleteHTML(total);
    return;
  }

  _reviewMergeTarget = null;
  const c = unreviewed[_reviewIndex];
  const rep = c.representative;
  const cid = c.cluster_id;
  const suggestion = c.suggested_match;
  overlay.innerHTML = reviewPanelHTML({
    progress: reviewed + _reviewIndex,
    total,
    photoCount: c.photo_count,
    avatarHTML: suggestion ? suggestionAvatarHTML(rep, suggestion) : singleAvatarHTML(rep),
    samplesHTML: reviewSamplesHTML(c),
    actionHTML: suggestion
      ? suggestionActionsHTML(suggestion, cid)
      : manualActionsHTML(cid, personDisplayName(cid) || ""),
  });

  // Focus name input if in manual mode (no suggestion)
  if (!suggestion) {
    setTimeout(() => {
      const inp = /** @type {HTMLInputElement|null} */ (
        document.getElementById("review-name-field")
      );
      if (inp) { inp.focus(); inp.select(); }
    }, 100);
  }
}

/**
 * Filter the existing-people list as the user types a name in manual
 * mode, rendering matches into the autocomplete dropdown.
 * (Full-photo preview moved to photo-preview.mjs — openPhotoPreview.)
 * @param {string} query
 */
export function _reviewAutocomplete(query) {
  _reviewMergeTarget = null;
  const dropdown = document.getElementById("review-autocomplete");
  if (!dropdown) return;
  const q = query.trim().toLowerCase();
  if (q.length < 2) { dropdown.innerHTML = ""; return; }

  // Find existing named people matching the query
  const currentCid = _reviewData.unreviewed[_reviewIndex]?.cluster_id;
  const matches = state.albumList
    .filter(a => a.album_type === "smart_person" && a.rule?.cluster_id !== currentCid)
    .filter(a => {
      const name = a.name || "";
      return !/^Person \d+$/.test(name) && name.toLowerCase().includes(q);
    })
    .slice(0, 5);

  if (matches.length === 0) { dropdown.innerHTML = ""; return; }

  dropdown.innerHTML = matches.map(a =>
    `<div class="review-ac-item" data-action="_reviewSelectMerge" data-arg0="${a.rule.cluster_id}" data-arg1="${escapeAttr(a.name)}">
      Merge into <strong>${esc(a.name)}</strong>
    </div>`
  ).join("");
}

export function _reviewSelectMerge(targetCid, name) {
  _reviewMergeTarget = { cluster_id: targetCid, name };
  const inp = /** @type {HTMLInputElement|null} */ (
    document.getElementById("review-name-field")
  );
  if (inp) inp.value = name;
  const dropdown = document.getElementById("review-autocomplete");
  if (dropdown) dropdown.innerHTML = "";
}

export function _reviewInputKey(event) {
  if (event.key === "Enter") _reviewConfirm();
  if (event.key === "Escape") {
    const dropdown = document.getElementById("review-autocomplete");
    if (dropdown) dropdown.innerHTML = "";
  }
}

export function _reviewShowManual() {
  const suggestion = /** @type {HTMLElement|null} */ (
    document.querySelector(".review-suggestion")
  );
  const manual = document.getElementById("review-manual");
  if (suggestion) suggestion.style.display = "none";
  if (manual) manual.style.display = "block";
  setTimeout(() => {
    const inp = /** @type {HTMLInputElement|null} */ (
      document.getElementById("review-name-field")
    );
    if (inp) { inp.focus(); inp.select(); }
  }, 50);
}

export async function _reviewMergeInto(targetCid, targetName) {
  const c = _reviewData.unreviewed[_reviewIndex];
  const cid = c.cluster_id;
  try {
    await apiFetch("/api/v1/faces/merge", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        primary_cluster_id: targetCid,
        merge_cluster_ids: [cid],
      }),
    });
    await loadAlbumList();
    const n = (c.filepaths || c.faces || []).length;
    const detail = n ? ` (${n} photo${n !== 1 ? "s" : ""})` : "";
    toast(`Merged into "${targetName}"${detail}`, "success");
    _reviewData.reviewed++;
    _reviewIndex++;
    _renderReviewCard();
  } catch (e) {
    toastError("merge the people", e);
  }
}

export async function _reviewConfirm() {
  const inp = /** @type {HTMLInputElement|null} */ (
    document.getElementById("review-name-field")
  );
  const name = inp ? inp.value.trim() : "";
  if (!name) {
    toast("Enter a name to confirm", "warning");
    inp?.focus();
    return;
  }
  const c = _reviewData.unreviewed[_reviewIndex];
  const cid = c.cluster_id;

  if (_reviewMergeTarget) {
    // Merge this cluster into the existing person
    try {
      await apiFetch("/api/v1/faces/merge", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          primary_cluster_id: _reviewMergeTarget.cluster_id,
          merge_cluster_ids: [cid],
        }),
      });
      await loadAlbumList();
      const mn = (c.filepaths || c.faces || []).length;
      const mdetail = mn ? ` (${mn} photo${mn !== 1 ? "s" : ""})` : "";
      toast(`Merged into "${_reviewMergeTarget.name}"${mdetail}`, "success");
    } catch (e) {
      toastError("merge the people", e);
      return;
    }
  } else {
    // Name as new person
    try {
      await refreshSmartAlbums();
      const albumId = getPersonAlbumId(cid);
      if (albumId) {
        await apiFetch(`/api/v1/albums/${albumId}`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ name }),
        });
      }
      await loadAlbumList();
    } catch (e) {
      toastError("name the person", e);
      return;
    }
    const nn = (c.filepaths || c.faces || []).length;
    const ndetail = nn ? ` — ${nn} photo${nn !== 1 ? "s" : ""}` : "";
    toast(`Named as "${name}"${ndetail}`, "success");
  }

  _reviewMergeTarget = null;
  _reviewData.reviewed++;
  _reviewIndex++;
  _renderReviewCard();
}

/** Test-only: put the review at a given cluster. */
export function _setReviewState(data, index) {
  _reviewData = data;
  _reviewIndex = index;
  _reviewMergeTarget = null;
}

export function _reviewSkip() {
  _reviewIndex++;
  _renderReviewCard();
}

export async function _reviewDismiss(clusterId) {
  try {
    await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ cluster_id: clusterId }),
    });
    toast("Dismissed — won\u2019t appear in People anymore");
    _reviewData.unreviewed.splice(_reviewIndex, 1);
    _reviewData.total--;
    _renderReviewCard();
  } catch (e) {
    toastError("dismiss that", e);
  }
}

// ── Person Album Action Bar ──
// Shown above the photo grid when viewing a smart_person album.




import {
  _closePairReviewOverlay,
  _pairReviewKeyHandler,
  _pairSkip,
  _pairVerdict,
  _renderPairReviewCard,
  _showPairReviewOverlay,
  getAmbiguousPairCount,
  refreshAmbiguousPairCount,
  startFacePairReview,
} from "./people-pair-review.mjs";
export {
  _closePairReviewOverlay,
  _pairReviewKeyHandler,
  _pairSkip,
  _pairVerdict,
  _renderPairReviewCard,
  _showPairReviewOverlay,
  getAmbiguousPairCount,
  refreshAmbiguousPairCount,
  startFacePairReview,
};
