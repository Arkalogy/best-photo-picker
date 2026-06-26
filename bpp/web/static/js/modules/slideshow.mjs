// @ts-check
/**
 * Slideshow overlay — stepping through `currentGridItems` with crossfade,
 * Ken Burns animation, optional shuffle, optional info overlay, and
 * keyboard control. Self-attaches a global `keydown` listener on import.
 *
 * Reads `state.currentGridItems` (still in classic globals) and mutates
 * `window.ICONS` to inject the slideshow-specific SVG icons. Lightbox.js
 * gates its own keyboard handler on `_isSlideshowActive()`.
 */

import { authedSrc } from "./api-client.mjs";
import { esc } from "./text-format.mjs";
import { state } from "./state.mjs";
import { toast } from "./toast.mjs";

let slideshowActive = false;
/** @type {ReturnType<typeof setTimeout> | null} */
let slideshowTimer = null;
let slideshowIdx = 0;
/** @type {any[]} */
let slideshowItems = [];
let slideshowInterval = 3000;
let slideshowPlaying = false;
let slideshowShuffle = false;
let slideshowShowInfo = false;
let slideshowKenBurns = true;
/** @type {number[]} */
let slideshowShuffleOrder = [];

/** @returns {boolean} */
export function _isSlideshowActive() {
  return slideshowActive;
}

/**
 * Open the slideshow overlay starting at `startIdx` in
 * `state.currentGridItems`, or 0 if not provided.
 *
 * @param {number} [startIdx]
 */
export function startSlideshow(startIdx) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (items.length === 0) {
    toast("No photos to show", true);
    return;
  }
  slideshowItems = [...items];
  if (slideshowItems.length === 0) {
    toast("No photos for slideshow", true);
    return;
  }

  if (startIdx != null && startIdx >= 0 && startIdx < items.length) {
    const fp = items[startIdx].filepath;
    const mapped = slideshowItems.findIndex((p) => p.filepath === fp);
    slideshowIdx = mapped >= 0 ? mapped : 0;
  } else {
    slideshowIdx = 0;
  }

  slideshowShuffleOrder = Array.from({ length: slideshowItems.length }, (_, i) => i);
  if (slideshowShuffle) _shuffleArray(slideshowShuffleOrder);

  slideshowActive = true;
  slideshowPlaying = true;

  const overlay = document.getElementById("slideshow-overlay");
  if (overlay) overlay.classList.remove("hidden");
  try {
    document.documentElement.requestFullscreen?.();
  } catch {
    /* noop */
  }

  _slideshowShow(slideshowIdx);
  _slideshowSchedule();
}

export function stopSlideshow() {
  slideshowActive = false;
  slideshowPlaying = false;
  if (slideshowTimer) clearTimeout(slideshowTimer);
  slideshowTimer = null;

  const overlay = document.getElementById("slideshow-overlay");
  if (overlay) overlay.classList.add("hidden");

  const imgA = document.getElementById("ss-img-a");
  const imgB = document.getElementById("ss-img-b");
  if (imgA) imgA.className = "ss-img";
  if (imgB) imgB.className = "ss-img";

  const ssVideo = /** @type {HTMLVideoElement | null} */ (document.getElementById("ss-video"));
  if (ssVideo) {
    ssVideo.pause();
    ssVideo.removeAttribute("src");
    ssVideo.onended = null;
    ssVideo.classList.add("hidden");
  }

  try {
    if (document.fullscreenElement) document.exitFullscreen?.();
  } catch {
    /* noop */
  }
}

/**
 * @param {number} idx
 */
export function _slideshowShow(idx) {
  if (idx < 0 || idx >= slideshowItems.length) return;
  slideshowIdx = idx;
  const p = slideshowItems[idx];
  const ssVideo = /** @type {HTMLVideoElement | null} */ (document.getElementById("ss-video"));

  if (p.is_video) {
    _slideshowShowVideo(p, ssVideo);
    _slideshowUpdateInfo(p);
    _slideshowUpdateCounter();
    return;
  }

  if (ssVideo) {
    ssVideo.pause();
    ssVideo.removeAttribute("src");
    ssVideo.classList.add("hidden");
  }

  const imgA = /** @type {HTMLImageElement | null} */ (document.getElementById("ss-img-a"));
  const imgB = /** @type {HTMLImageElement | null} */ (document.getElementById("ss-img-b"));
  if (!imgA || !imgB) return;
  const isAActive = imgA.classList.contains("ss-active");
  const incoming = isAActive ? imgB : imgA;
  const outgoing = isAActive ? imgA : imgB;

  incoming.src = authedSrc("/photo/" + p.thumb_hash);
  incoming.onload = () => {
    incoming.classList.add("ss-active");
    outgoing.classList.remove("ss-active");
    if (slideshowKenBurns) {
      incoming.classList.remove("ss-kb-1", "ss-kb-2", "ss-kb-3", "ss-kb-4");
      const variant = "ss-kb-" + ((idx % 4) + 1);
      void incoming.offsetWidth;
      incoming.classList.add(variant);
    }
  };
  if (incoming.complete && incoming.src.includes(p.thumb_hash)) {
    incoming.classList.add("ss-active");
    outgoing.classList.remove("ss-active");
    if (slideshowKenBurns) {
      incoming.classList.remove("ss-kb-1", "ss-kb-2", "ss-kb-3", "ss-kb-4");
      void incoming.offsetWidth;
      incoming.classList.add("ss-kb-" + ((idx % 4) + 1));
    }
  }

  _slideshowUpdateInfo(p);
  _slideshowUpdateCounter();
}

/**
 * @param {any} p
 * @param {HTMLVideoElement | null} videoEl
 */
function _slideshowShowVideo(p, videoEl) {
  if (!videoEl) return;
  const imgA = document.getElementById("ss-img-a");
  const imgB = document.getElementById("ss-img-b");
  if (imgA) imgA.classList.remove("ss-active");
  if (imgB) imgB.classList.remove("ss-active");

  videoEl.classList.remove("hidden");
  videoEl.poster = authedSrc("/thumb/" + p.thumb_hash);
  videoEl.src = authedSrc("/video/" + p.thumb_hash);
  videoEl.play().catch((e) => console.warn("Slideshow video autoplay blocked:", e));

  if (slideshowTimer) clearTimeout(slideshowTimer);
  slideshowTimer = null;

  videoEl.onended = () => {
    videoEl.classList.add("hidden");
    videoEl.removeAttribute("src");
    if (slideshowActive) {
      slideshowNav(1);
      if (slideshowPlaying) _slideshowSchedule();
    }
  };
}

/**
 * @param {any} p
 */
function _slideshowUpdateInfo(p) {
  const info = document.getElementById("ss-info");
  if (!info) return;
  if (slideshowShowInfo) {
    info.classList.remove("hidden");
    const date = p.date || p.date_day || "";
    const name = p.filename || "";
    info.innerHTML = `<div class="ss-info-name">${esc(name)}</div><div class="ss-info-date">${esc(date)}</div>`;
  } else {
    info.classList.add("hidden");
  }
}

function _slideshowUpdateCounter() {
  const el = document.getElementById("ss-counter");
  if (el) el.textContent = slideshowIdx + 1 + " / " + slideshowItems.length;
}

function _slideshowSchedule() {
  if (slideshowTimer) clearTimeout(slideshowTimer);
  if (!slideshowPlaying || !slideshowActive) return;
  slideshowTimer = setTimeout(() => {
    slideshowNav(1);
    _slideshowSchedule();
  }, slideshowInterval);
}

/**
 * Navigate slideshow by `dir` (-1 prev, +1 next), wrapping at ends.
 *
 * @param {number} dir
 */
export function slideshowNav(dir) {
  if (!slideshowActive) return;
  let nextIdx;
  if (slideshowShuffle) {
    const curPos = slideshowShuffleOrder.indexOf(slideshowIdx);
    const nextPos = curPos + dir;
    if (nextPos < 0 || nextPos >= slideshowShuffleOrder.length) {
      nextIdx = slideshowShuffleOrder[dir > 0 ? 0 : slideshowShuffleOrder.length - 1];
    } else {
      nextIdx = slideshowShuffleOrder[nextPos];
    }
  } else {
    nextIdx = slideshowIdx + dir;
    if (nextIdx >= slideshowItems.length) nextIdx = 0;
    if (nextIdx < 0) nextIdx = slideshowItems.length - 1;
  }
  _slideshowShow(nextIdx);
  if (slideshowPlaying) _slideshowSchedule();
}

export function slideshowTogglePlay() {
  /** @type {any} */
  const win = window;
  slideshowPlaying = !slideshowPlaying;
  const btn = document.getElementById("ss-play-btn");
  if (btn) btn.innerHTML = slideshowPlaying ? win.ICONS?.ssPause : win.ICONS?.ssPlay;
  if (slideshowPlaying) _slideshowSchedule();
  else if (slideshowTimer) clearTimeout(slideshowTimer);
}

/**
 * @param {number} ms
 */
export function slideshowSetSpeed(ms) {
  slideshowInterval = ms;
  document.querySelectorAll(".ss-speed-btn").forEach((el) => {
    const speed = parseInt(/** @type {HTMLElement} */ (el).dataset.speed || "0");
    el.classList.toggle("active", speed === ms);
  });
  if (slideshowPlaying) _slideshowSchedule();
}

export function slideshowToggleShuffle() {
  slideshowShuffle = !slideshowShuffle;
  const btn = document.getElementById("ss-shuffle-btn");
  if (btn) btn.classList.toggle("active", slideshowShuffle);
  if (slideshowShuffle) {
    slideshowShuffleOrder = Array.from({ length: slideshowItems.length }, (_, i) => i);
    _shuffleArray(slideshowShuffleOrder);
  }
}

export function slideshowToggleInfo() {
  slideshowShowInfo = !slideshowShowInfo;
  const btn = document.getElementById("ss-info-btn");
  if (btn) btn.classList.toggle("active", slideshowShowInfo);
  if (slideshowActive && slideshowItems[slideshowIdx]) {
    _slideshowUpdateInfo(slideshowItems[slideshowIdx]);
  }
}

export function slideshowToggleKenBurns() {
  slideshowKenBurns = !slideshowKenBurns;
  const btn = document.getElementById("ss-kb-btn");
  if (btn) btn.classList.toggle("active", slideshowKenBurns);
  if (!slideshowKenBurns) {
    document
      .getElementById("ss-img-a")
      ?.classList.remove("ss-kb-1", "ss-kb-2", "ss-kb-3", "ss-kb-4");
    document
      .getElementById("ss-img-b")
      ?.classList.remove("ss-kb-1", "ss-kb-2", "ss-kb-3", "ss-kb-4");
  }
}

/**
 * Fisher-Yates shuffle in place.
 *
 * @param {number[]} arr
 */
export function _shuffleArray(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
}

/**
 * Test-only: reset all internal slideshow state to defaults.
 */
export function _resetSlideshowState() {
  slideshowActive = false;
  if (slideshowTimer) clearTimeout(slideshowTimer);
  slideshowTimer = null;
  slideshowIdx = 0;
  slideshowItems = [];
  slideshowInterval = 3000;
  slideshowPlaying = false;
  slideshowShuffle = false;
  slideshowShowInfo = false;
  slideshowKenBurns = true;
  slideshowShuffleOrder = [];
}

document.addEventListener("keydown", (e) => {
  if (!slideshowActive) return;
  if (e.key === "Escape") {
    e.preventDefault();
    // Stop sibling bubble handlers (compare, calendar) from also
    // processing this ESC. Dialog (capture phase) already runs first
    // and uses stopImmediatePropagation, so this never blocks dialog.
    e.stopImmediatePropagation();
    stopSlideshow();
    return;
  }
  if (e.key === " ") {
    e.preventDefault();
    slideshowTogglePlay();
    return;
  }
  if (e.key === "ArrowRight") {
    e.preventDefault();
    slideshowNav(1);
    return;
  }
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    slideshowNav(-1);
    return;
  }
  if (e.key === "i" || e.key === "I") {
    e.preventDefault();
    slideshowToggleInfo();
    return;
  }
  if (e.key === "k" || e.key === "K") {
    e.preventDefault();
    slideshowToggleKenBurns();
    return;
  }
  if (e.key === "s" || e.key === "S") {
    e.preventDefault();
    slideshowToggleShuffle();
    return;
  }
});

// Inject slideshow-specific icons into the global ICONS dict.
/** @type {any} */
const _win = window;
_win.ICONS = _win.ICONS || {};
_win.ICONS.ssPlay = '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11l9-5.5z"/></svg>';
_win.ICONS.ssPause =
  '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="2" width="3.5" height="12" rx="1"/><rect x="9.5" y="2" width="3.5" height="12" rx="1"/></svg>';
_win.ICONS.ssShuffle =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h3l4-8h3M2 4h3l4 8h3M12 3v3h-3M12 13v-3h-3"/></svg>';
_win.ICONS.ssInfo =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><line x1="8" y1="7" x2="8" y2="11"/><circle cx="8" cy="5" r="0.5" fill="currentColor"/></svg>';
_win.ICONS.ssKenBurns =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="14" height="10" rx="1.5"/><rect x="3" y="5" width="10" height="6" rx="1" stroke-dasharray="2 1.5"/></svg>';
_win.ICONS.ssSlideshow =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="2" width="14" height="10" rx="1.5"/><path d="M6.5 5v4l3.5-2z" fill="currentColor" stroke="none"/><line x1="4" y1="14" x2="12" y2="14"/></svg>';
