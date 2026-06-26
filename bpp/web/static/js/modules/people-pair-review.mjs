// @ts-check
/**
 * Ambiguous-pair face review wizard.
 *
 * Extracted from people-review.mjs during the v0.1 cleanup. Walks
 * cluster pairs whose centroid distance is below the merge threshold
 * (0.75). The user records same / different / skip verdicts. "Same"
 * MERGES the pair (named/larger cluster wins) and teaches the adaptive
 * threshold; the toast's Undo reverses the merge from a server-provided
 * snapshot. "Different" blocks future auto-merges via hard_negatives.
 *
 * Re-exported from people-review.mjs and (transitively) from people.mjs.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { loadFaceClusters } from "./faces.mjs";
import {toast, toastError} from "./toast.mjs";
import { personDisplayName } from "./people.mjs";
import { reviewMetaLine } from "./review-meta.mjs";


// ── Face Pair Review Flow ──
// Walks through ambiguous cluster pairs (centroid distance below 0.75) and
// records same/different verdicts. "Same" merges the pair on the spot
// (and teaches the threshold via face_feedback); "different" blocks future
// auto-merges of genuinely different clusters via hard_negatives.

let _pairReviewData = null;
let _pairReviewIndex = 0;
let _pairReviewStats = {same: 0, different: 0, skipped: 0};
// Per-side "show the whole photo" toggle — tight crops (profiles, backs of
// heads) are often impossible to judge without surrounding context. Reset
// on every pair advance so each pair starts at the face crop.
let _pairZoomedOut = {a: false, b: false};
// Cache for the "Review pairs (N)" button label. null = not yet fetched.
let _ambiguousPairCount = null;

export function getAmbiguousPairCount() {
  return _ambiguousPairCount;
}

export async function refreshAmbiguousPairCount() {
  try {
    const resp = await apiFetch("/api/v1/faces/review-pairs/count");
    _ambiguousPairCount = resp && typeof resp.count === "number" ? resp.count : 0;
  } catch {
    _ambiguousPairCount = 0;
  }
  // Patch just the button in place — avoids re-rendering the whole grid
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-review-pairs")
  );
  if (btn) {
    btn.textContent = `Review pairs (${_ambiguousPairCount})`;
    btn.disabled = _ambiguousPairCount === 0;
  }
}

export async function startFacePairReview() {
  try {
    _pairReviewData = await apiFetch("/api/v1/faces/review-pairs/next?limit=30");
  } catch (e) {
    toastError("load ambiguous pairs", e);
    return;
  }
  if (!_pairReviewData.pairs || _pairReviewData.pairs.length === 0) {
    toast("No ambiguous pairs — clusters look clean");
    return;
  }
  _pairReviewIndex = 0;
  _pairReviewStats = {same: 0, different: 0, skipped: 0};
  _showPairReviewOverlay();
}

export function _showPairReviewOverlay() {
  let overlay = document.getElementById("face-pair-review-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "face-pair-review-overlay";
    overlay.className = "modal-overlay";
    document.body.appendChild(overlay);
  }
  overlay.classList.add("visible");
  _renderPairReviewCard();
}

export function _closePairReviewOverlay() {
  const overlay = document.getElementById("face-pair-review-overlay");
  if (overlay) overlay.classList.remove("visible");
  document.removeEventListener("keydown", _pairReviewKeyHandler);
  const s = _pairReviewStats;
  const total = s.same + s.different + s.skipped;
  if (total > 0) {
    const plural = total !== 1 ? "s" : "";
    const skipPart = s.skipped ? `, ${s.skipped} skipped` : "";
    toast(
      `Reviewed ${total} pair${plural} — ${s.same} same, ${s.different} different${skipPart}`,
      "success"
    );
  }
  // Refresh the People view to reflect any cluster metadata changes,
  // and update the Review pairs button count.
  loadFaceClusters();
  refreshAmbiguousPairCount();
}

// Tracks which pair the zoom state belongs to; a different index means
// we advanced (or undid) — start that pair back at the face crops.
let _pairZoomIndex = -1;

export function _renderPairReviewCard() {
  const overlay = document.getElementById("face-pair-review-overlay");
  if (!overlay || !_pairReviewData) return;

  if (_pairZoomIndex !== _pairReviewIndex) {
    _pairZoomedOut = {a: false, b: false};
    _pairZoomIndex = _pairReviewIndex;
  }

  const {pairs, threshold} = _pairReviewData;
  if (_pairReviewIndex >= pairs.length) {
    _renderBatchDoneCard(overlay);
    return;
  }

  const pair = pairs[_pairReviewIndex];
  const total = pairs.length;
  const cur = _pairReviewIndex + 1;

  const cropUrl = (c) => c.representative.thumb_hash
    ? authedSrc(`/api/v1/faces/crop/${esc(c.representative.thumb_hash)}/${c.representative.face_index}`)
    : "";
  const fullUrl = (c) => c.representative.thumb_hash
    ? authedSrc(`/thumb/${esc(c.representative.thumb_hash)}`)
    : "";

  const card = (c, side) => {
    const zoomedOut = _pairZoomedOut[side];
    const tip = zoomedOut
      ? "Click to zoom back in on the face (Z toggles both)"
      : "Click to see the whole photo for context (Z toggles both)";
    return `
    <div class="pair-review-card">
      <div class="pair-review-face${zoomedOut ? " pair-zoomed-out" : ""}"
           data-action="_pairToggleContext" data-arg0="${side}" title="${tip}">
        <img src="${escapeAttr(zoomedOut ? fullUrl(c) : cropUrl(c))}" alt="${escapeAttr(c.name)}">
      </div>
      <div class="pair-review-name">${esc(c.name)}</div>
      <div class="pair-review-count">${c.face_count} face${c.face_count !== 1 ? "s" : ""} in ${c.photo_count} photo${c.photo_count !== 1 ? "s" : ""}</div>
      ${reviewMetaLine(c.representative)}
    </div>`;
  };

  overlay.innerHTML = `
    <div class="pair-review-panel">
      <div class="pair-review-header">
        <h3>Same person?</h3>
        <div class="pair-review-progress">${cur} of ${total}</div>
      </div>
      <div class="pair-review-bodies">
        ${card(pair.cluster_a, "a")}
        <div class="pair-review-vs">?</div>
        ${card(pair.cluster_b, "b")}
      </div>
      <div class="pair-review-meta">
        <span title="How far apart these two groups' average face signatures are. 0 = identical; smaller = more alike. Pairs are presented closest-first.">Distance: ${pair.distance.toFixed(3)}</span>
        &middot;
        <span title="The cutoff the app uses when grouping faces automatically: faces closer than this are treated as the same person. Pairs near it are too close to call without you.">Threshold: ${threshold.toFixed(3)}</span>
        <span class="pair-review-info" title="Distance is how far apart these two groups' average face signatures are (smaller = more alike). Threshold is the app's automatic same-person cutoff. Pairs too close to call are shown here, closest-first.">&#9432;</span>
      </div>
      <div class="pair-review-actions">
        <button class="pair-review-btn pair-same" data-action="_pairVerdict" data-arg0="same">&#10003; Same person <kbd>S</kbd></button>
        <button class="pair-review-btn pair-different" data-action="_pairVerdict" data-arg0="different">&#10005; Different <kbd>D</kbd></button>
        <button class="pair-review-btn" data-action="_pairSkip">Skip <kbd>&rarr;</kbd></button>
        <button class="pair-review-btn" data-action="_closePairReviewOverlay">Close <kbd>Esc</kbd></button>
      </div>
    </div>
  `;

  document.removeEventListener("keydown", _pairReviewKeyHandler);
  document.addEventListener("keydown", _pairReviewKeyHandler);
}

export async function _pairVerdict(verdict) {
  const pair = _pairReviewData.pairs[_pairReviewIndex];
  if (!pair) return;
  const pairIndex = _pairReviewIndex;
  const win = window;
  try {
    const resp = await apiFetch("/api/v1/faces/review-pairs/verdict", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        cluster_a: pair.cluster_a.id,
        cluster_b: pair.cluster_b.id,
        verdict: verdict,
      }),
    });
    _pairReviewStats[verdict]++;
    _pairReviewIndex++;
    let label;
    if (verdict === "same" && resp && resp.merged) {
      // "Same person" actually merged the clusters. Reflect it now:
      // sidebar albums update, and any later pair in this batch that
      // referenced the absorbed (now-gone) cluster is stale — drop it.
      if (resp.albums) {
        win.albumList = resp.albums;
        win.renderAlbumNav?.();
      }
      const absorbed = resp.absorbed_cluster_id;
      _pairReviewData.pairs = _pairReviewData.pairs.filter(
        (p, i) =>
          i < _pairReviewIndex ||
          (p.cluster_a.id !== absorbed && p.cluster_b.id !== absorbed),
      );
      const primarySide = resp.primary_cluster_id === pair.cluster_a.id
        ? pair.cluster_a : pair.cluster_b;
      const absorbedSide = resp.primary_cluster_id === pair.cluster_a.id
        ? pair.cluster_b : pair.cluster_a;
      const n = absorbedSide.face_count;
      label = `${absorbedSide.name}'s ${n} face${n === 1 ? "" : "s"} moved into ` +
        `${primarySide.name} — ${absorbedSide.name} is gone from People`;
    } else {
      // Name the pair — with rapid verdicts, an anonymous toast leaves
      // the user unsure WHICH decision an Undo would revert.
      const names = `${pair.cluster_a.name} + ${pair.cluster_b.name}`;
      label = verdict === "same"
        ? `${names}: same person`
        : `${names}: different people`;
    }
    _renderPairReviewCard();
    toast(label, undefined, {
      action: {
        label: "Undo",
        fn: () => _pairVerdictUndo(pair, verdict, pairIndex, resp?.undo),
      },
    });
  } catch (e) {
    toastError("record the verdict", e);
  }
}

/**
 * Undo a just-recorded verdict (toast action): reverts the feedback
 * server-side — for "same" verdicts the merge itself is reversed from
 * the snapshot — and, when the review overlay is still open, steps back
 * to the undone pair so the user can re-answer.
 * @param {any} pair
 * @param {"same"|"different"} verdict
 * @param {number} pairIndex
 * @param {any} [undoSnapshot] merge-undo snapshot from the verdict response
 */
async function _pairVerdictUndo(pair, verdict, pairIndex, undoSnapshot) {
  const win = window;
  try {
    const resp = await apiFetch("/api/v1/faces/review-pairs/verdict/undo", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        cluster_a: pair.cluster_a.id,
        cluster_b: pair.cluster_b.id,
        verdict: verdict,
        ...(undoSnapshot ? {undo: undoSnapshot} : {}),
      }),
    });
    if (resp && resp.albums) {
      win.albumList = resp.albums;
      win.renderAlbumNav?.();
    }
    if (_pairReviewStats[verdict] > 0) _pairReviewStats[verdict]--;
    const overlay = document.getElementById("face-pair-review-overlay");
    if (overlay && overlay.classList.contains("visible")) {
      _pairReviewIndex = pairIndex;
      _renderPairReviewCard();
    }
    toast(verdict === "same" && undoSnapshot ? "Merge undone" : "Verdict undone");
  } catch (e) {
    toastError("undo that verdict", e);
  }
}

export function _pairSkip() {
  _pairReviewStats.skipped++;
  _pairReviewIndex++;
  _renderPairReviewCard();
}

/**
 * End-of-batch card: session totals + how many pairs remain, with
 * Continue (next batch) / Done. Replaces the old behavior of silently
 * closing the overlay after pair 30 of 30.
 * @param {HTMLElement} overlay
 */
function _renderBatchDoneCard(overlay) {
  const s = _pairReviewStats;
  const total = s.same + s.different + s.skipped;
  overlay.innerHTML = `
    <div class="pair-review-panel">
      <div class="pair-review-header">
        <h3>Batch done</h3>
      </div>
      <div class="pair-review-complete">
        <div>You reviewed ${total} pair${total === 1 ? "" : "s"} this session &mdash;
          ${s.same} same, ${s.different} different${s.skipped ? `, ${s.skipped} skipped` : ""}.</div>
        <div id="pair-review-remaining">Checking for more&hellip;</div>
      </div>
      <div class="pair-review-actions">
        <button class="pair-review-btn" data-action="_pairReviewContinue"
                id="pair-review-continue-btn" disabled>Continue <kbd>&crarr;</kbd></button>
        <button class="pair-review-btn" data-action="_closePairReviewOverlay">Done <kbd>Esc</kbd></button>
      </div>
    </div>
  `;
  // Fill in the remaining count asynchronously — skipped pairs come back,
  // answered pairs don't (merged or hard-negatived).
  apiFetch("/api/v1/faces/review-pairs/count")
    .then((resp) => {
      const n = resp && typeof resp.count === "number" ? resp.count : 0;
      const remainingEl = document.getElementById("pair-review-remaining");
      const btn = /** @type {HTMLButtonElement | null} */ (
        document.getElementById("pair-review-continue-btn")
      );
      if (!remainingEl || !btn) return;
      if (n > 0) {
        remainingEl.innerHTML = `<strong>${n}</strong> pair${n === 1 ? "" : "s"} left to review.`;
        btn.disabled = false;
        btn.innerHTML = `Continue (next ${Math.min(30, n)}) <kbd>&crarr;</kbd>`;
      } else {
        remainingEl.textContent = "That was all of them — no ambiguous pairs left.";
        btn.remove();
      }
    })
    .catch(() => {
      const remainingEl = document.getElementById("pair-review-remaining");
      if (remainingEl) remainingEl.textContent = "Couldn't check for more pairs.";
    });
}

/** Continue with the next batch — keeps the session stats running. */
export async function _pairReviewContinue() {
  let data;
  try {
    data = await apiFetch("/api/v1/faces/review-pairs/next?limit=30");
  } catch (e) {
    toastError("load the next batch of pairs", e);
    return;
  }
  if (!data.pairs || data.pairs.length === 0) {
    _closePairReviewOverlay();
    return;
  }
  _pairReviewData = data;
  _pairReviewIndex = 0;
  _renderPairReviewCard();
}

/**
 * Toggle one side between the tight face crop and the full photo.
 * @param {"a"|"b"} side
 */
export function _pairToggleContext(side) {
  if (side !== "a" && side !== "b") return;
  _pairZoomedOut[side] = !_pairZoomedOut[side];
  _renderPairReviewCard();
}

export function _pairReviewKeyHandler(e) {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  const overlay = document.getElementById("face-pair-review-overlay");
  if (!overlay || !overlay.classList.contains("visible")) return;
  // Batch-done card: Enter continues (when enabled), Esc closes below.
  const continueBtn = /** @type {HTMLButtonElement|null} */ (
    document.getElementById("pair-review-continue-btn")
  );
  if (continueBtn && e.key === "Enter") {
    e.preventDefault();
    if (!continueBtn.disabled) _pairReviewContinue();
    return;
  }
  switch (e.key) {
    case "s": case "S":
      e.preventDefault(); _pairVerdict("same"); break;
    case "d": case "D":
      e.preventDefault(); _pairVerdict("different"); break;
    case "z": case "Z": {
      // Toggle both sides together — if either is still the crop, zoom
      // both out; if both are already out, zoom both back in.
      e.preventDefault();
      const out = !(_pairZoomedOut.a && _pairZoomedOut.b);
      _pairZoomedOut = {a: out, b: out};
      _renderPairReviewCard();
      break;
    }
    case "ArrowRight":
      e.preventDefault(); _pairSkip(); break;
    case "Escape":
      e.preventDefault(); _closePairReviewOverlay(); break;
  }
}
