// @ts-check
/**
 * The HTML of one face-review card (`_renderReviewCard` in
 * people-review.mjs): the "done" panel, the avatar block (side-by-side
 * when there is a suggested match), the action block (suggestion or
 * manual naming) and the panel frame. Split out for the 500-LOC cap.
 */

import { authedSrc } from "./api-client.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { reviewMetaLine, reviewMetaText } from "./review-meta.mjs";

/**
 * Build `data-arg1..3` (filename/date/score) for an openPhotoPreview crop,
 * so the full-photo preview can caption itself. Empty string when no meta.
 * @param {{filename?:string, date?:string, score?:number}|null|undefined} m
 * @returns {string}
 */
export function _previewArgs(m) {
  if (!m) return "";
  return (
    ` data-arg1="${escapeAttr(m.filename || "")}"` +
    ` data-arg2="${escapeAttr(m.date || "")}"` +
    ` data-arg3="${escapeAttr(m.score == null ? "" : String(m.score))}"`
  );
}

/** @param {number} total */
export function reviewCompleteHTML(total) {
  return `<div class="review-panel">
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
}

/**
 * Sample photo grid — each opens the full photo so the user can
 * actually judge "is this Kai?" (the round avatar is a tiny face crop).
 * Tooltip carries filename · timestamp · score so a thumbnail is
 * self-describing. Back-compat: old `sample_hashes` (list of strings).
 * @param {any} c
 */
export function reviewSamplesHTML(c) {
  const sampleItems = c.samples
    || (c.sample_hashes || []).map((/** @type {string} */ h) => ({ hash: h }));
  return sampleItems.map((/** @type {any} */ s) => {
    const tip = reviewMetaText(s) || "Click to see the full photo";
    return `<img class="review-sample-img review-clickable" src="${authedSrc("/thumb/" + s.hash)}" loading="lazy"
       data-action="openPhotoPreview" data-arg0="${escapeAttr(s.hash)}"${_previewArgs(s)} title="${escapeAttr(tip)}">`;
  }).join("");
}

/** @param {any} rep */
function _unknownAvatarUrl(rep) {
  return authedSrc("/api/v1/faces/crop/" + esc(rep.thumb_hash) + "/" + rep.face_index);
}

/**
 * Side-by-side avatar comparison with confidence.
 * @param {any} rep
 * @param {any} suggestion
 */
export function suggestionAvatarHTML(rep, suggestion) {
  const matchRep = suggestion.representative;
  const matchAvatarUrl = matchRep
    ? authedSrc("/api/v1/faces/crop/" + esc(matchRep.thumb_hash) + "/" + matchRep.face_index)
    : "";
  const conf = suggestion.confidence || 0;
  return `
      <div class="review-avatar-compare">
        <div class="review-avatar-side">
          <img class="review-clickable" src="${_unknownAvatarUrl(rep)}"
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
}

/**
 * No suggestion — single avatar.
 * @param {any} rep
 */
export function singleAvatarHTML(rep) {
  return `
      <div class="review-avatar">
        <img class="review-clickable" src="${_unknownAvatarUrl(rep)}"
          data-action="openPhotoPreview" data-arg0="${escapeAttr(rep.thumb_hash)}"${_previewArgs(rep)}
          title="Click to see the full photo">
        ${reviewMetaLine(rep)}
      </div>`;
}

/**
 * The manual-naming block: a name input with autocomplete and the
 * confirm / skip / dismiss actions.
 * @param {number} cid
 * @param {string} currentName
 * @param {string} [extraAttrs]  on the wrapper, e.g. an id and display:none
 */
export function manualActionsHTML(cid, currentName, extraAttrs = "") {
  return `
      <div class="review-manual"${extraAttrs}>
        <div class="review-name-input">
          <input type="text" id="review-name-field" placeholder="Type name or search existing…"
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

/**
 * "Is this X?" with merge / no / skip / dismiss, plus the hidden manual
 * block "No" reveals.
 * @param {any} suggestion
 * @param {number} cid
 */
export function suggestionActionsHTML(suggestion, cid) {
  return `
      <div class="review-suggestion">
        <div class="review-suggestion-label">Is this <strong>${esc(suggestion.name)}</strong>?</div>
        <div class="review-actions">
          <button class="btn btn-primary" title="Combine these faces into one person"
            data-action="_reviewMergeInto" data-arg0="${suggestion.cluster_id}" data-arg1="${escapeJsAttr(suggestion.name)}">Yes, merge</button>
          <button class="btn btn-secondary" title="Not the same person — let me name manually"
            data-action="_reviewShowManual">No</button>
          <button class="btn btn-secondary" title="Skip for now, come back later"
            data-action="_reviewSkip">Skip</button>
          <button class="btn btn-secondary btn-danger-text" title="Hide this face permanently"
            data-action="_reviewDismiss" data-arg0="${cid}">Dismiss</button>
        </div>
      </div>` + manualActionsHTML(cid, "", ' id="review-manual" style="display:none"');
}

/**
 * @param {object} p
 * @param {number} p.progress  reviewed so far (0-based position)
 * @param {number} p.total
 * @param {number} p.photoCount
 * @param {string} p.avatarHTML
 * @param {string} p.samplesHTML
 * @param {string} p.actionHTML
 */
export function reviewPanelHTML({ progress, total, photoCount, avatarHTML, samplesHTML, actionHTML }) {
  const pct = ((progress / total) * 100).toFixed(0);
  return `<div class="review-panel">
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
        <div class="review-cluster-label">${photoCount} photo${photoCount !== 1 ? 's' : ''}</div>
        <div class="review-sample-grid">${samplesHTML}</div>
      </div>
      ${actionHTML}
    </div>
  </div>`;
}
