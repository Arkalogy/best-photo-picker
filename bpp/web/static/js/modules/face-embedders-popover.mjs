// @ts-check
/**
 * Overflow (⋯) menu + license-info popover plumbing for the picker rows,
 * plus the document-level ESC / click-outside handlers.
 *
 * Split out of ``modals-face-embedders.mjs`` for the 500-LOC cap. Shares
 * picker state via ``feState`` and closes the acceptance dialog on
 * ESC/click-out (``closeFaceEmbedderAcceptance``). The import cycle with
 * the acceptance + picker modules is safe — all cross-referenced symbols
 * are hoisted function declarations invoked at runtime.
 */

import { closeFaceEmbedderAcceptance } from "./face-embedders-acceptance.mjs";
import { feState } from "./face-embedders-state.mjs";
import { escapeAttr, esc } from "./text-format.mjs";
import { toast } from "./toast.mjs";

/**
 * Toggle a row's overflow menu. Refuses to open when the row is
 * mid-operation so stale enabled actions (Redownload, Use, Uninstall)
 * can't be clicked during a download / uninstall in flight.
 *
 * @param {string} entryId
 */
export function _feOverflowToggle(entryId) {
  const menu = document.getElementById("fe-ovr-" + entryId);
  if (!menu) return;
  if (feState.busyRowIds.has(entryId) && !menu.classList.contains("open")) {
    // Surface why the menu won't open — silently refusing reads as
    // a UI glitch and the user will keep clicking.
    toast(
      "This model is busy with another operation. Wait for it to finish.",
    );
    return;
  }
  // Close any other open menus + close any open license popover.
  const wasOpen = menu.classList.contains("open");
  _feCloseAllPopovers(menu);
  if (wasOpen) {
    menu.classList.remove("open", "fe-overflow-menu--up");
    return;
  }
  // Open downward by default, then flip up if it would be clipped by the
  // bottom of its scroll container / viewport — otherwise the last rows'
  // menu items (e.g. Download) get cut off and become unreachable.
  menu.classList.remove("fe-overflow-menu--up");
  menu.classList.add("open");
  try {
    const rect = menu.getBoundingClientRect();
    if (rect.bottom > _feMenuClipBottom(menu) - 8) {
      menu.classList.add("fe-overflow-menu--up");
    }
  } catch {
    /* positioning is best-effort; default (down) still works */
  }
}

/**
 * Smallest bottom edge (viewport px) that an open overflow menu must fit
 * within: the viewport, narrowed by any scroll/clip ancestor. Used to
 * decide whether the menu should flip upward.
 * @param {Element} menu
 * @returns {number}
 */
function _feMenuClipBottom(menu) {
  let limit = window.innerHeight;
  let el = menu.parentElement;
  while (el && el !== document.body) {
    const oy = getComputedStyle(el).overflowY;
    if (oy === "auto" || oy === "scroll" || oy === "hidden") {
      limit = Math.min(limit, el.getBoundingClientRect().bottom);
    }
    el = el.parentElement;
  }
  return limit;
}

/**
 * Toggle the small license-summary popover attached to a row's ⓘ.
 * Built lazily — the popover element is created the first time the
 * user clicks ⓘ on that row, then reused.
 *
 * @param {string} entryId
 * @param {string} licenseSummary
 */
export function _feLicenseInfoToggle(entryId, licenseSummary) {
  const popId = "fe-info-" + entryId;
  let pop = document.getElementById(popId);
  if (pop) {
    pop.classList.toggle("open");
    if (pop.classList.contains("open")) _feCloseAllPopovers(pop);
    return;
  }
  // Create on first click. Anchor it to the ⓘ trigger.
  const trigger = document.querySelector(
    `.fe-license-tip[data-arg0='${entryId}']`,
  );
  if (!trigger) return;
  pop = document.createElement("div");
  pop.id = popId;
  pop.className = "fe-license-popover open";
  pop.textContent = licenseSummary || "(no license summary)";
  trigger.parentElement?.appendChild(pop);
  _feCloseAllPopovers(pop);
}

/**
 * Close every overflow menu + license popover, except an optional
 * one to leave open (so toggling a new one doesn't immediately
 * close it again).
 * @param {Element | null} [keepOpen]
 */
function _feCloseAllPopovers(keepOpen) {
  for (const open of document.querySelectorAll(
    ".fe-overflow-menu.open, .fe-license-popover.open",
  )) {
    if (open !== keepOpen) open.classList.remove("open", "fe-overflow-menu--up");
  }
}

/** Document-level handlers: ESC closes the dialog AND any open
 *  popover; click outside any open popover closes it too; click on
 *  the acceptance dialog's overlay backdrop dismisses the dialog.
 *  Installed once, idempotent. */
export function _feInstallGlobalHandlers() {
  if (/** @type {any} */ (window).__feGlobalHandlersInstalled) return;
  /** @type {any} */ (window).__feGlobalHandlersInstalled = true;
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    // Acceptance dialog takes precedence over popovers — it's the
    // foreground modal when both are open.
    const overlay = document.getElementById("fe-acceptance-overlay");
    if (overlay && overlay.classList.contains("visible")) {
      closeFaceEmbedderAcceptance();
      e.stopPropagation();
      return;
    }
    const any = document.querySelector(
      ".fe-overflow-menu.open, .fe-license-popover.open",
    );
    if (any) {
      _feCloseAllPopovers();
      e.stopPropagation();
    }
  });
  document.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    if (!target) return;
    // Acceptance dialog: clicking directly on the overlay backdrop
    // (NOT on the inner modal) dismisses, matching every other modal
    // in BPP. The modal element stops the bubble so clicks inside
    // the modal never reach the overlay.
    const overlay = document.getElementById("fe-acceptance-overlay");
    if (
      overlay &&
      overlay.classList.contains("visible") &&
      target === overlay
    ) {
      closeFaceEmbedderAcceptance();
      return;
    }
    // If the click landed on a trigger or inside a still-open popover, leave it.
    if (
      target.closest(".fe-overflow-trigger") ||
      target.closest(".fe-overflow-menu") ||
      target.closest(".fe-license-tip") ||
      target.closest(".fe-license-popover")
    ) {
      return;
    }
    _feCloseAllPopovers();
  });
}
