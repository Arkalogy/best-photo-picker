// @ts-check
/**
 * App bootstrap module — replaces the classic app.js script tag.
 *
 * Wires global error boundaries, the DOMContentLoaded init flow that
 * boots theme + sliders + tooltips + context menus + analyze status,
 * the saved-navigation restore logic, and the throttled scroll/resize
 * handlers that drive vgrid.
 *
 * Side-effect-only on import: registers listeners + (if the DOM is
 * already parsed when this module runs) immediately kicks off init.
 *
 * Most helpers it calls are window-bridged (classic-side or already
 * module-bridged via index.html's `Object.assign(window, ...)` block);
 * a few are imported directly.
 */

import { apiFetch } from "./api-client.mjs";
import {
  doRecompute,
  listenProgress,
  loadPhotosAndRecompute,
  showEmptyLibrary,
  showTuningState,
} from "./analysis.mjs";
import { initCardCtxMenu } from "./deleted.mjs";
import { initMomentBurstFlyout } from "./moments-stacks.mjs";
import { formatVal } from "./format-helpers.mjs";
import { getSavedNavState, updateBreadcrumbs } from "./navigation.mjs";
import {
  loadFaceClusters,
  listenFaceProgress,
  refreshSmartAlbums,
  startFaceExtraction,
  updateFaceStatus,
  updateFaceThresholdLabel,
} from "./faces.mjs";
import { getSetting, loadSettings } from "./settings-client.mjs";
import { initActivityLog } from "./activity-log.mjs";
import { monitorPhashBackfill } from "./phash-status.mjs";
import { beaconClientError } from "./client-error-beacon.mjs";
import { initToolbarIcons } from "./toolbar.mjs";
import { initSliders, initTooltips, updateContentFilterLabel } from "./ui-helpers.mjs";
import { setSensitiveMode } from "./sensitive.mjs";
import { initTheme } from "./theme.mjs";
import { initUpdateChecker } from "./update-checker.mjs";
import { loadMemories, openMemory } from "./memories.mjs";
import { loadOnThisDay } from "./on-this-day.mjs";
import {
  loadOverridesFromDB,
  navigateToFavorites,
  navigateToPeople,
  startStorageHealthCheck,
  switchToLibrary,
} from "./core.mjs";
import { maybeAutoStartTour } from "./tour.mjs";
import { navigateToCalendar } from "./calendar.mjs";
import { navigateToDeleted, navigateToHidden } from "./deleted.mjs";
import { show } from "./utils.mjs";
import { showOnboarding } from "./onboarding.mjs";
import { toast, toastError } from "./toast.mjs";
import { updateClipStatusFromAppStatus } from "./clip.mjs";

/**
 * Browser-noise filter — these messages reach window.onerror but are
 * NOT real errors. ResizeObserver in particular fires
 * 'ResizeObserver loop completed with undelivered notifications' as a
 * W3C-spec notification whenever a callback triggers another layout
 * change; every production web app filters it. Same goes for the
 * 'Script error.' opaque cross-origin message that browsers emit when
 * a third-party script (extension, embedded iframe) throws.
 *
 * Exported only so tests-js/app-error-noise.module.test.mjs can pin
 * the filter contract — never reach for this from runtime code.
 *
 * @param {string | Event} message
 * @returns {boolean}
 */
export function _isBrowserNoise(message) {
  if (typeof message !== "string") return false;
  return (
    message.includes("ResizeObserver loop") ||
    message === "Script error." ||
    message === "Script error"
  );
}

function _viewLogToast() {
  toast("Something went wrong — that action didn't complete. Please try again.", true, {
    action: {
      label: "View log",
      fn: () => {
        const win = /** @type {any} */ (window);
        if (typeof win.showActivityLog === "function") win.showActivityLog();
      },
    },
  });
}

window.onerror = function (message, source, lineno, colno, error) {
  if (_isBrowserNoise(message)) return true; // swallow without surfacing
  // Keep full detail in the console + activity log for debugging,
  // but never surface raw stack traces to end users — they read as
  // 'this app is broken' even when it's recoverable. Friendly toast +
  // an Activity Log shortcut so a curious operator can dig in.
  console.error("Uncaught error:", message, source, lineno, colno, error);
  // Beacon to the server log so the error shows in Activity — that's what
  // makes "View log" meaningful for a client-side error now.
  beaconClientError({
    message: String(message),
    source: String(source || ""),
    lineno: Number(lineno) || 0,
    colno: Number(colno) || 0,
    stack: (/** @type {any} */ (error) && /** @type {any} */ (error).stack) || "",
  });
  _viewLogToast();
  _recoverVisibleView();
  return true;
};

function _recoverVisibleView() {
  const views = ["photo-grid", "people-view", "pets-view", "groups-view", "tags-view", "calendar-view", "map-view"];
  const anyVisible = views.some((id) => {
    const el = document.getElementById(id);
    return el && !el.classList.contains("hidden");
  });
  if (!anyVisible) {
    console.warn("No view visible — recovering to photo grid");
    show("photo-grid");
  }
}

window.addEventListener("unhandledrejection", (e) => {
  // Same UX policy as window.onerror — friendly toast + Activity Log
  // shortcut. Raw rejection.message often carries server-side text
  // that's already toasted by the caller; for genuinely uncaught
  // rejections (programmer bugs in await chains) we don't want the
  // user to see internals.
  const reason = /** @type {any} */ (e.reason);
  const reasonMsg = reason?.message || String(reason || "");
  if (_isBrowserNoise(reasonMsg)) return;
  console.warn("Unhandled rejection:", reason);
  beaconClientError({
    message: reasonMsg || "unhandled rejection",
    stack: (reason && reason.stack) || "",
  });
  _viewLogToast();
  _recoverVisibleView();
});

async function _bootstrap() {
  /** @type {any} */
  const win = window;
  try {
    await loadSettings();
    initTheme();
    win.applyZoom?.(getSetting("zoom_pct", "80"), false);
    // Boost choices persist like any setting — restore before the first
    // face-gallery render so chips/cards show the saved selection.
    win.restoreBoostSelection?.();
    win.sidebarFaceSort = getSetting("sidebar_face_sort", "count");

    document.title = (win.APP_CONFIG && win.APP_CONFIG.name) || "Best Photo Picker";
    win.updateLibraryName?.();
    initToolbarIcons();
    initSliders();
    initTooltips();
    win.initPersonCtxMenu?.();
    win.initPetCtxMenu?.();
    initCardCtxMenu();
    initMomentBurstFlyout();
    await loadOverridesFromDB();
    win.loadAllTags?.();
    const st = await apiFetch("/api/v1/status");
    win.state_workdir = st.workdir || null;

    if (st.defaults) {
      // Cached for re-renders: renderAlbumNav rebuilds the sidebar (and
      // its boost slider) and needs the saved values to restore from.
      win.state_defaults = st.defaults;
      document.querySelectorAll("[data-param]").forEach((el) => {
        const elH = /** @type {HTMLElement} */ (el);
        const key = elH.dataset.param;
        if (key && st.defaults[key] !== undefined) {
          /** @type {HTMLInputElement} */ (el).value = st.defaults[key];
          const sib = /** @type {HTMLElement | null} */ (el.nextElementSibling);
          if (sib) sib.textContent = formatVal(key, st.defaults[key]);
        }
      });
      // Sensitive-photo policy rides its own 2-way control, not the
      // [data-param] float bus — hydrate it explicitly.
      if (st.defaults.sensitive_in_picks !== undefined) {
        setSensitiveMode(st.defaults.sensitive_in_picks);
      }
    }

    win.faceRecognitionAvailable = !!st.face_recognition_available;
    win.faceInstallable = !!st.face_installable;
    win.nudenetAvailable = !!st.nudenet_available;
    win.petsAvailable = !!st.pets_available;
    updateContentFilterLabel();
    updateClipStatusFromAppStatus(st);
    // Surface the background phash/dedup backfill in the status bar
    // (fire-and-forget; self-stops when the backfill finishes or if none
    // is running). It was a silent machine-pegging op before.
    monitorPhashBackfill();
    if (st.face_extraction_done && win.faceRecognitionAvailable) {
      const section = /** @type {HTMLElement | null} */ (
        document.getElementById("settings-face-section")
      );
      if (section) section.style.display = "block";
      if (st.face_cluster_threshold) {
        const slider = /** @type {HTMLInputElement | null} */ (
          document.getElementById("face-cluster-slider")
        );
        if (slider) slider.value = String(st.face_cluster_threshold);
        updateFaceThresholdLabel(st.face_cluster_threshold);
      }
    }

    if (st.has_analysis) {
      // @anchor:bootstrap:tuning-start
      showTuningState();
      // showTuningState fires loadAlbumList() but doesn't await it. The
      // savedNav restoration below needs win.albumList populated so it
      // can look up the saved album by id — without this await, the
      // lookup falls through to loadPhotosAndRecompute() and the user
      // lands on Library on every refresh. Catch failures so a transient
      // /api/albums error doesn't reject _bootstrap entirely — savedNav
      // restoration just falls back to Library, which is the same UX as
      // before the await was added.
      try {
        await win.loadAlbumList?.();
      } catch (e) {
        console.warn("loadAlbumList failed during bootstrap:", e);
      }

      // @anchor:bootstrap:savednav-read
      const savedNav = getSavedNavState();
      const albums = /** @type {any[]} */ (win.albumList || []);
      if (!win.currentAlbumId && albums.length > 0) {
        const allAlbum = albums.find((a) => a.album_type === "all");
        if (allAlbum) win.currentAlbumId = allAlbum.id;
        win.renderAlbumNav?.();
      }

      /** @type {string | null} */
      let _deferredNav = null;

      const photosReady = (async () => {
        let needsBackgroundRecompute = true;
        if (savedNav && savedNav.view) {
          if (savedNav.view === "album" && savedNav.albumId) {
            const allAlbum = albums.find((a) => a.album_type === "all");
            if (allAlbum && savedNav.albumId === allAlbum.id) {
              await loadPhotosAndRecompute();
              needsBackgroundRecompute = false;
            } else if (win.switchAlbum && albums.some((a) => a.id === savedNav.albumId)) {
              await win.switchAlbum(savedNav.albumId);
            } else {
              await loadPhotosAndRecompute();
              needsBackgroundRecompute = false;
            }
          } else if (
            savedNav.view === "people" ||
            savedNav.view === "pets" ||
            savedNav.view === "groups"
          ) {
            _deferredNav = savedNav.view;
          } else if (savedNav.view === "picks") {
            await loadPhotosAndRecompute();
            needsBackgroundRecompute = false;
            const filterEl = /** @type {HTMLInputElement | null} */ (
              document.getElementById("filter-by")
            );
            if (filterEl) filterEl.value = "selected";
            win.renderGrid?.();
            updateBreadcrumbs("BPP Picks", "Library", "switchToLibrary()");
            win.currentView = "picks";
          } else if (savedNav.view === "favorites") {
            navigateToFavorites();
          } else if (savedNav.view === "deleted") {
            await navigateToDeleted();
          } else if (savedNav.view === "hidden") {
            await navigateToHidden();
          } else if (savedNav.view === "memory" && savedNav.viewId) {
            await openMemory(/** @type {any} */ (savedNav.viewId));
          } else if (savedNav.view === "calendar") {
            navigateToCalendar();
          } else if (savedNav.view === "map") {
            await loadPhotosAndRecompute();
            needsBackgroundRecompute = false;
            win.navigateToMap?.();
          } else {
            await loadPhotosAndRecompute();
            needsBackgroundRecompute = false;
          }
          // Restore active filter (e.g. "enhanced", "favorites") if it was saved.
          if (savedNav.filter && savedNav.filter !== "all") {
            const filterEl = /** @type {HTMLInputElement | null} */ (
              document.getElementById("filter-by")
            );
            if (filterEl) { filterEl.value = savedNav.filter; win.renderGrid?.(); }
          }
          const items = /** @type {any[]} */ (win.currentGridItems || []);
          if (savedNav.lightboxPath && items.length > 0) {
            const lbIdx = items.findIndex((p) => p.filepath === savedNav.lightboxPath);
            if (lbIdx >= 0) win.openLightbox?.(lbIdx);
          }
        } else {
          await loadPhotosAndRecompute();
          needsBackgroundRecompute = false;
        }
        if (needsBackgroundRecompute) {
          const allAlbum = albums.find((a) => a.album_type === "all");
          if (allAlbum) {
            doRecompute({ delta: true })
              .then(() => {
                win.renderAlbumNav?.();
              })
              .catch((e) => console.warn("Background recompute failed:", e));
          }
        }
      })();

      const sidebarReady = (async () => {
        if (st.pet_detection_done && win.loadPetClusters) await win.loadPetClusters();
        loadOnThisDay();
        loadMemories();
        win.loadTagsList?.().then(() => win.renderAlbumNav?.());
        if (st.face_extraction_done) {
          await loadFaceClusters();
          if (win.loadGroups) await win.loadGroups();
          await refreshSmartAlbums();
        } else if (win.faceRecognitionAvailable && !st.face_extracting) {
          startFaceExtraction();
        } else if (st.face_extracting) {
          listenFaceProgress();
        } else {
          updateFaceStatus();
        }
      })();

      await Promise.all([photosReady, sidebarReady]);

      if (_deferredNav === "people") navigateToPeople();
      else if (_deferredNav === "pets") win.navigateToPets?.();
      else if (_deferredNav === "groups") win.navigateToGroups?.();
    } else if (st.analyzing) {
      listenProgress();
    } else if (st.first_run) {
      showOnboarding(st.library_path);
    } else {
      showEmptyLibrary();
    }

    if (!st.has_analysis && !st.first_run) maybeAutoStartTour();

    startStorageHealthCheck();
    win.startServerHealthCheck?.();
    initUpdateChecker();
    initActivityLog();
  } catch (e) {
    console.error("Startup failed:", e);
    toastError("start the app", e);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _bootstrap);
} else {
  // Module scripts are deferred — DOMContentLoaded may have already fired.
  _bootstrap();
}

// Throttle scroll to one rAF per frame (~60fps) to avoid DOM thrashing
let _scrollRaf = 0;
const _content = document.querySelector(".content");
if (_content) {
  _content.addEventListener("scroll", () => {
    /** @type {any} */
    const win = window;
    win.hideCardCtxMenu?.();
    if (!_scrollRaf) {
      _scrollRaf = requestAnimationFrame(() => {
        win.vgrid?.onScroll();
        _scrollRaf = 0;
      });
    }
  });

  // Re-render the virtual grid whenever .content changes size.
  new ResizeObserver(() => {
    /** @type {any} */ (window).vgrid?.onResize();
  }).observe(_content);
}

let _resizeTimer = 0;
window.addEventListener("resize", () => {
  /** @type {any} */
  const win = window;
  clearTimeout(_resizeTimer);
  _resizeTimer = /** @type {any} */ (
    setTimeout(() => {
      win.vgrid?.onResize();
    }, 150)
  );
});

// Make switchToLibrary callable from inline onclick handlers (it's used
// by breadcrumb links rendered in updateBreadcrumbs).
/** @type {any} */ (window).switchToLibrary = switchToLibrary;
