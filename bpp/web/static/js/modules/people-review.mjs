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

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters, refreshSmartAlbums } from "./faces.mjs";
import { toast, toastError } from "./toast.mjs";
import { getPersonAlbumId, getPersonName, personDisplayName } from "./people.mjs";
import { reviewMetaLine, reviewMetaText } from "./review-meta.mjs";


/**
 * Build `data-arg1..3` (filename/date/score) for an openPhotoPreview crop,
 * so the full-photo preview can caption itself. Empty string when no meta.
 * @param {{filename?:string, date?:string, score?:number}|null|undefined} m
 * @returns {string}
 */
function _previewArgs(m) {
  if (!m) return "";
  return (
    ` data-arg1="${escapeAttr(m.filename || "")}"` +
    ` data-arg2="${escapeAttr(m.date || "")}"` +
    ` data-arg3="${escapeAttr(m.score == null ? "" : String(m.score))}"`
  );
}


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
    overlay.innerHTML = `<div class="review-panel">
      <div class="review-header">
        <h2>Review Complete</h2>
        <button class="review-close-btn" data-action="_closeReviewOverlay">&times;</button>
      </div>
      <div class="review-done">
        <div class="review-done-icon">&#10003;</div>
        <p>All ${total} people have been reviewed.</p>
        <button class="btn btn-primary" data-action="_closeReviewOverlay">Done</button>
      </div>
    </div>`;
    return;
  }

  _reviewMergeTarget = null;
  const c = unreviewed[_reviewIndex];
  const rep = c.representative;
  const cid = c.cluster_id;
  const progress = reviewed + _reviewIndex;
  const pct = ((progress / total) * 100).toFixed(0);
  const currentName = personDisplayName(cid) || "";
  const defaultName = getPersonName(cid) || `Person ${cid + 1}`;

  // Build sample photo grid — each opens the full photo so the user can
  // actually judge "is this Leo?" (the round avatar is a tiny face crop).
  // Tooltip carries filename · timestamp · score so a thumbnail is
  // self-describing. Back-compat: old `sample_hashes` (list of strings).
  const sampleItems = c.samples
    || (c.sample_hashes || []).map((/** @type {string} */ h) => ({ hash: h }));
  const samples = sampleItems.map((/** @type {any} */ s) => {
    const tip = reviewMetaText(s) || "Click to see the full photo";
    return `<img class="review-sample-img review-clickable" src="${authedSrc("/thumb/" + s.hash)}" loading="lazy"
       data-action="openPhotoPreview" data-arg0="${escapeAttr(s.hash)}"${_previewArgs(s)} title="${escapeAttr(tip)}">`;
  }).join("");

  const suggestion = c.suggested_match;
  let actionHTML;
  let avatarHTML;
  const unknownAvatarUrl = authedSrc(
    "/api/v1/faces/crop/" + esc(rep.thumb_hash) + "/" + rep.face_index
  );

  if (suggestion) {
    // Build side-by-side avatar comparison with confidence
    const matchRep = suggestion.representative;
    const matchAvatarUrl = matchRep
      ? authedSrc("/api/v1/faces/crop/" + esc(matchRep.thumb_hash) + "/" + matchRep.face_index)
      : "";
    const conf = suggestion.confidence || 0;
    avatarHTML = `
      <div class="review-avatar-compare">
        <div class="review-avatar-side">
          <img class="review-clickable" src="${unknownAvatarUrl}"
            data-action="openPhotoPreview" data-arg0="${escapeAttr(rep.thumb_hash)}"${_previewArgs(rep)}
            title="Click to see the full photo">
          <div class="review-avatar-label">Unknown</div>
          ${reviewMetaLine(rep)}
        </div>
        <div class="review-match-indicator">
          <div class="review-match-pct">${conf}%</div>
          <div class="review-match-label">match</div>
        </div>
        <div class="review-avatar-side">
          <img class="${matchRep ? "review-clickable" : ""}" src="${matchAvatarUrl}"
            ${matchRep ? `data-action="openPhotoPreview" data-arg0="${escapeAttr(matchRep.thumb_hash)}"${_previewArgs(matchRep)} title="Click to see the full photo"` : ""}>
          <div class="review-avatar-label is-person">${esc(suggestion.name)}</div>
          ${reviewMetaLine(matchRep)}
        </div>
      </div>`;
    actionHTML = `
      <div class="review-suggestion">
        <div class="review-suggestion-label">Is this <strong>${esc(suggestion.name)}</strong>?</div>
        <div class="review-actions">
          <button class="btn btn-primary" title="Combine these faces into one person"
            data-action="_reviewMergeInto" data-arg0="${suggestion.cluster_id}" data-arg1="${escapeJsAttr(suggestion.name)}">Yes, merge</button>
          <button class="btn btn-secondary" title="Not the same person \u2014 let me name manually"
            data-action="_reviewShowManual">No</button>
          <button class="btn btn-secondary" title="Skip for now, come back later"
            data-action="_reviewSkip">Skip</button>
          <button class="btn btn-secondary btn-danger-text" title="Hide this face permanently"
            data-action="_reviewDismiss" data-arg0="${cid}">Dismiss</button>
        </div>
      </div>
      <div class="review-manual" id="review-manual" style="display:none">
        <div class="review-name-input">
          <input type="text" id="review-name-field" placeholder="Type name or search existing\u2026"
            value="" autocomplete="off"
            data-oninput="_reviewAutocomplete"
            data-onkeydown="_reviewInputKey">
          <div class="review-autocomplete" id="review-autocomplete"></div>
        </div>
        <div class="review-actions">
          <button class="btn btn-primary" title="Assign this name and move to next"
            data-action="_reviewConfirm">Name &amp; Confirm</button>
          <button class="btn btn-secondary" title="Skip for now, come back later"
            data-action="_reviewSkip">Skip</button>
          <button class="btn btn-secondary btn-danger-text" title="Hide this face permanently"
            data-action="_reviewDismiss" data-arg0="${cid}">Dismiss</button>
        </div>
      </div>`;
  } else {
    // No suggestion — single avatar, manual mode directly
    avatarHTML = `
      <div class="review-avatar">
        <img class="review-clickable" src="${unknownAvatarUrl}"
          data-action="openPhotoPreview" data-arg0="${escapeAttr(rep.thumb_hash)}"${_previewArgs(rep)}
          title="Click to see the full photo">
        ${reviewMetaLine(rep)}
      </div>`;
    actionHTML = `
      <div class="review-manual">
        <div class="review-name-input">
          <input type="text" id="review-name-field" placeholder="Type name or search existing\u2026"
            value="${escapeAttr(currentName)}" autocomplete="off"
            data-oninput="_reviewAutocomplete"
            data-onkeydown="_reviewInputKey">
          <div class="review-autocomplete" id="review-autocomplete"></div>
        </div>
        <div class="review-actions">
          <button class="btn btn-primary" title="Assign this name and move to next"
            data-action="_reviewConfirm">Name &amp; Confirm</button>
          <button class="btn btn-secondary" title="Skip for now, come back later"
            data-action="_reviewSkip">Skip</button>
          <button class="btn btn-secondary btn-danger-text" title="Hide this face permanently"
            data-action="_reviewDismiss" data-arg0="${cid}">Dismiss</button>
        </div>
      </div>`;
  }

  overlay.innerHTML = `<div class="review-panel">
    <div class="review-header">
      <h2>Review People</h2>
      <span class="review-progress">${progress + 1} of ${total}</span>
      <button class="review-close-btn" title="Close review"
        data-action="_closeReviewOverlay">&times;</button>
    </div>
    <div class="review-progress-bar">
      <div class="review-progress-fill" style="width: ${pct}%"></div>
    </div>
    <div class="review-body">
      ${avatarHTML}
      <div class="review-info">
        <div class="review-cluster-label">${c.photo_count} photo${c.photo_count !== 1 ? 's' : ''}</div>
        <div class="review-sample-grid">${samples}</div>
      </div>
      ${actionHTML}
    </div>
  </div>`;

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
    const nn = (c.filepaths || c.faces || []).length;
    const ndetail = nn ? ` — ${nn} photo${nn !== 1 ? "s" : ""}` : "";
    toast(`Named as "${name}"${ndetail}`, "success");
  }

  _reviewMergeTarget = null;
  _reviewData.reviewed++;
  _reviewIndex++;
  _renderReviewCard();
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
