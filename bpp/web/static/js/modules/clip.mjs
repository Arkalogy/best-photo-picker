// @ts-check
/**
 * CLIP embedding extraction trigger + status UI + the Lightbox
 * "similar photos" strip that consumes CLIP-derived siblings.
 *
 * The compute flow is:
 *  - User clicks Compute → POST /api/clip/extract
 *  - Server starts; we open SSE on /api/clip/progress
 *  - SSE messages drive the badge / progress fill / status row
 *  - On `done`: refresh adaptive-threshold info, schedule recompute
 *
 * Heavy DOM coupling. Bridged onto window because callers in
 * analysis.js / app.js / faces.js / lightbox.js / inline onclick
 * still reference these names.
 */

import { apiFetch, authEventSource, authedSrc } from "./api-client.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { escapeAttr } from "./text-format.mjs";
import { showToast, toast, toastError } from "./toast.mjs";

/**
 * Kick off CLIP embedding extraction. Hides the trigger button,
 * shows progress, opens the SSE consumer.
 */
export async function startClipExtraction() {
  const btn = document.getElementById("btn-clip-extract");
  const prog = document.getElementById("clip-progress");
  if (btn) btn.classList.add("hidden");
  if (prog) prog.classList.remove("hidden");

  /** @type {any} */
  const win = window;
  win.showStatusProgress?.("Computing semantic search index…", 0);

  try {
    await apiFetch("/api/v1/clip/extract", { method: "POST" });
  } catch (e) {
    toastError("start the semantic search index", e);
    if (btn) btn.classList.remove("hidden");
    if (prog) prog.classList.add("hidden");
    win.hideStatusProgress?.();
    return;
  }

  const es = authEventSource("/api/v1/clip/progress");
  es.onmessage = (event) => {
    const msg = /** @type {any} */ (parseSSE(event.data));
    if (!msg) return;
    if (msg.type === "progress") {
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      const clipText = document.getElementById("clip-progress-text");
      if (clipText) clipText.textContent = `Computing... ${msg.current}/${msg.total}`;
      const clipFill = /** @type {HTMLElement | null} */ (
        document.getElementById("clip-progress-fill")
      );
      if (clipFill) clipFill.style.width = pct + "%";
      win.showStatusProgress?.(`CLIP ${msg.current}/${msg.total}`, pct);
    } else if (msg.type === "done") {
      es.close();
      if (prog) prog.classList.add("hidden");
      updateClipStatus(true);
      win.hideStatusProgress?.();
      win._analyzeStop?.();
      win._showAnalyzeSummary?.();
      win.scheduleRecompute?.();
    } else if (msg.type === "error") {
      es.close();
      if (prog) prog.classList.add("hidden");
      if (btn) btn.classList.remove("hidden");
      win.hideStatusProgress?.();
      win._analyzeStop?.();
      showToast("CLIP extraction failed: " + msg.message);
    }
  };
  es.onerror = () => {
    es.close();
    if (prog) prog.classList.add("hidden");
    if (btn) btn.classList.remove("hidden");
    win.hideStatusProgress?.();
  };
}

/**
 * Update the inline CLIP status row (badge + button visibility +
 * adaptive-info + dim hash controls when CLIP is on).
 *
 * @param {boolean} ready
 */
export function updateClipStatus(ready) {
  const badge = document.getElementById("clip-status-badge");
  const btn = document.getElementById("btn-clip-extract");
  const adaptiveInfo = document.getElementById("dedup-adaptive-info");
  const hashControls = /** @type {HTMLElement | null} */ (
    document.getElementById("hash-dedup-controls")
  );
  const desc = /** @type {HTMLElement | null} */ (
    document.getElementById("clip-status-desc")
  );
  document.getElementById("clip-status-row")?.classList.remove("hidden");
  if (!badge || !btn || !adaptiveInfo) return;
  if (ready) {
    badge.className = "clip-badge ready";
    badge.textContent = "Ready";
    btn.classList.add("hidden");
    if (desc) desc.style.display = "none";
    adaptiveInfo.classList.remove("hidden");
    if (hashControls) hashControls.style.opacity = "0.4";
  } else {
    badge.className = "clip-badge off";
    badge.textContent = "Off";
    if (desc) desc.style.display = "block";
    adaptiveInfo.classList.add("hidden");
    if (hashControls) hashControls.style.opacity = "1";
  }
}

/**
 * Render the memory-cap banner — three states surfaced by the backend:
 *  - "enabled": library under cap, no banner needed.
 *  - "disabled_too_large": library over cap, no override → render the
 *    "Enable anyway / Learn more" prompt with the peak-MB estimate.
 *  - "enabled_override": library over cap, override active → render a
 *    quieter status line with a "Disable" link.
 *
 * @param {{
 *   clip_cap_status?: "enabled" | "disabled_too_large" | "enabled_override",
 *   clip_cap?: number,
 *   clip_cap_peak_mb?: number,
 *   clip_embedding_count?: number,
 * }} status
 */
export function updateClipCapBanner(status) {
  const banner = document.getElementById("clip-cap-banner");
  const msg = document.getElementById("clip-cap-msg");
  const active = document.getElementById("clip-cap-active");
  const activeMsg = document.getElementById("clip-cap-active-msg");
  if (!banner || !active) return;
  const count = status.clip_embedding_count || 0;
  const peakMb = status.clip_cap_peak_mb || 0;
  const peakGb = (peakMb / 1024).toFixed(1);
  if (status.clip_cap_status === "disabled_too_large") {
    if (msg) {
      msg.textContent =
        `Your library has ${count.toLocaleString()} photos. ` +
        `Semantic deduplication needs ~${peakGb} GB RAM ` +
        `and is currently disabled — your other features still work.`;
    }
    banner.classList.remove("hidden");
    active.classList.add("hidden");
  } else if (status.clip_cap_status === "enabled_override") {
    if (activeMsg) {
      activeMsg.textContent =
        `Semantic dedup enabled despite the ${(status.clip_cap || 0).toLocaleString()}-photo cap ` +
        `— using ~${peakGb} GB RAM.`;
    }
    banner.classList.add("hidden");
    active.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
    active.classList.add("hidden");
  }
}

/** Toggle the inline "Learn more" expansion under the banner. */
export function toggleClipCapLearnMore() {
  const learn = document.getElementById("clip-cap-learn");
  if (!learn) return;
  learn.classList.toggle("hidden");
}

async function _setClipCapOverride(enable) {
  try {
    const data = /** @type {any} */ (
      await apiFetch("/api/v1/settings/clip_max_override", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enable }),
      })
    );
    updateClipCapBanner({
      clip_cap_status: data.clip_cap_status,
      clip_cap: data.clip_cap,
      clip_cap_peak_mb: data.clip_cap_peak_mb,
      // re-render needs the photo count too; the override response
      // doesn't ship it, but the active-state message only depends
      // on the cap + peak so an empty count is fine here.
      clip_embedding_count: 0,
    });
    toast(
      enable
        ? "Semantic dedup enabled — restart not required"
        : "Semantic dedup disabled — cap will apply on next load",
    );
  } catch (e) {
    console.warn("clip_max_override toggle failed:", e);
    toastError("save the setting", e);
  }
}

/** Approve the cap bypass for this library. */
export async function enableClipCapOverride() {
  await _setClipCapOverride(true);
}

/** Clear the cap bypass and re-apply the default. */
export async function disableClipCapOverride() {
  await _setClipCapOverride(false);
}

/**
 * Apply the CLIP status from an `/api/v1/status` payload — picks one
 * of: ready / computing / off / not-installed.
 *
 * @param {{
 *   clip_ready?: boolean,
 *   clip_embedding_count?: number,
 *   clip_extracting?: boolean,
 *   clip_available?: boolean,
 *   clip_installable?: boolean,
 *   clip_cap_status?: "enabled" | "disabled_too_large" | "enabled_override",
 *   clip_cap?: number,
 *   clip_cap_peak_mb?: number,
 * }} status
 */
export function updateClipStatusFromAppStatus(status) {
  // Banner is independent of the badge state: the cap can fire whether
  // CLIP is "ready", "extracting", or "off" — the user still needs the
  // opt-in path.
  updateClipCapBanner(status);
  const badge = document.getElementById("clip-status-badge");
  const btn = document.getElementById("btn-clip-extract");
  if (!badge || !btn) return;

  if (status.clip_ready && (status.clip_embedding_count || 0) > 0) {
    updateClipStatus(true);
  } else if (status.clip_extracting) {
    badge.className = "clip-badge computing";
    badge.textContent = "Computing...";
    btn.classList.add("hidden");
    document.getElementById("clip-progress")?.classList.remove("hidden");
  } else if (status.clip_available) {
    document.getElementById("clip-status-row")?.classList.remove("hidden");
    badge.className = "clip-badge off";
    badge.textContent = "Off";
    btn.classList.remove("hidden");
  } else if (status.clip_installable) {
    document.getElementById("clip-status-row")?.classList.remove("hidden");
    badge.className = "clip-badge off";
    badge.textContent = "Not installed";
    btn.textContent = "Install CLIP extra";
    btn.classList.remove("hidden");
    /** @type {HTMLButtonElement} */ (btn).onclick = () =>
      toast("CLIP dedup requires: pip install bppicker[clip]", true);
  }
}

/**
 * Render the dedup-stats panel — adaptive threshold + feedback count.
 *
 * @param {{
 *   clip_threshold?: number,
 *   clip_threshold_info?: { feedback_count?: number },
 * }} stats
 */
export function updateDedupStats(stats) {
  if (stats.clip_threshold !== undefined) {
    const thresholdEl = document.getElementById("dedup-threshold-val");
    if (thresholdEl) thresholdEl.textContent = stats.clip_threshold.toFixed(3);
    const info = stats.clip_threshold_info || {};
    const count = info.feedback_count || 0;
    const fbEl = document.getElementById("dedup-feedback-count");
    if (fbEl) {
      fbEl.textContent =
        count > 0
          ? count + " feedback signal" + (count === 1 ? "" : "s")
          : "default";
    }
    document.getElementById("dedup-adaptive-info")?.classList.remove("hidden");
  }
}

/**
 * Render the Lightbox "similar photos" strip from `p.similar_photos`.
 *
 * @param {{ thumb_hash?: string, similar_photos?: Array<{ thumb_hash: string, similarity: number|null }>, _isMoment?: boolean }} p
 */
export function updateLightboxSimilar(p) {
  const el = document.getElementById("lb-similar");
  const strip = document.getElementById("lb-similar-strip");
  if (!el || !strip) return;
  if (p.similar_photos && p.similar_photos.length > 0) {
    const n = p.similar_photos.length;
    const label = document.querySelector("#lb-similar .lb-similar-label");
    if (p._isMoment) {
      // Panel-cleanup item 6: one quiet header line + a compact strip of
      // ALL the Moment's shots (current photo first, highlighted), no
      // similarity badges. Review = step through in the compare overlay.
      if (label) {
        label.innerHTML =
          `<span>Part of a ${n + 1}-shot Moment</span>` +
          `<button class="lb-moment-review" data-action="_openSiblingCompare" data-arg0="0" ` +
          `title="Compare these shots side by side and keep the best">Review</button>`;
        label.classList.add("lb-moment-head");
      }
      strip.classList.add("lb-moment-strip");
      strip.innerHTML =
        `<div class="lb-similar-item lb-moment-current" title="This photo">` +
        `<img src="${authedSrc("/thumb/" + p.thumb_hash)}" alt="" data-onerror="_bppThumbBroken"></div>` +
        p.similar_photos
          .map(
            (s, i) =>
              `<div class="lb-similar-item" data-action="_openSiblingCompare" data-arg0="${i}" title="Compare with this shot">
        <img src="${authedSrc("/thumb/" + s.thumb_hash)}" alt="" data-onerror="_bppThumbBroken">
      </div>`,
          )
          .join("");
    } else {
      if (label) {
        label.textContent = `${n} similar photo${n > 1 ? "s" : ""} — click to compare`;
        label.classList.remove("lb-moment-head");
      }
      strip.classList.remove("lb-moment-strip");
      strip.innerHTML = p.similar_photos
        .map((s, i) => {
          const pct = s.similarity != null ? `${(s.similarity * 100).toFixed(0)}%` : "~";
          const title = s.similarity != null
            ? `${(s.similarity * 100).toFixed(0)}% similar — click to compare`
            : "Similar shot — click to compare";
          return `<div class="lb-similar-item" data-action="_openSiblingCompare" data-arg0="${i}" title="${escapeAttr(title)}">
        <img src="${authedSrc("/thumb/" + s.thumb_hash)}" alt="" data-onerror="_bppThumbBroken">
        <span class="lb-similar-pct">${pct}</span>
      </div>`;
        })
        .join("");
    }
    el.classList.remove("hidden");
  } else {
    el.classList.add("hidden");
  }
}

/**
 * Open the side-by-side compare view for the sibling at index `i` of
 * the currently-open lightbox photo.
 *
 * @param {number} siblingIdx
 */
export function _openSiblingCompare(siblingIdx) {
  /** @type {any} */
  const win = window;
  if (typeof win.lightboxIdx !== "number" || win.lightboxIdx < 0) return;
  const parent = win.currentGridItems?.[win.lightboxIdx];
  if (!parent || !parent.similar_photos) return;
  win.openCompareWithSibling?.(parent, parent.similar_photos, siblingIdx);
}

/**
 * Locate a photo by its filepath in the current grid and open the
 * lightbox at that index.
 *
 * @param {string} filepath
 */
export function openLightboxByPath(filepath) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const idx = items.findIndex((p) => p.filepath === filepath);
  if (idx >= 0) win.openLightbox?.(idx);
}
