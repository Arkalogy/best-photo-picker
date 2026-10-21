// @ts-check
/**
 * Keyboard and screen-reader behaviour for the app's menus (right-click
 * menus, the picker's ⋯ menu, the album menus): announced as a menu with
 * its live items, arrow keys move through it, Enter / Space pick, and
 * focus goes back to what opened it. Each menu calls ``menuOpened`` once
 * it is shown and positioned, and ``menuClosed`` when it hides; Escape
 * stays with the menu's own handler.
 */

const ITEM_SELECTOR = ".ctx-menu-item, .ctx-item, .fe-menu-item";
const FOCUSABLE = "button, [href], input, select, textarea, [tabindex]";

/** Per menu: what opened it and the key handler to remove. */
const _open = new WeakMap();
/** Per menu: what opened it, kept after it closed (a dialog opened from
 *  one of its items asks, once the menu has already hidden itself). */
const _lastTrigger = new WeakMap();

/**
 * The menu's live items: shown and enabled.
 * @param {HTMLElement} menu
 * @param {string} selector
 * @returns {HTMLElement[]}
 */
function _liveItems(menu, selector) {
  return /** @type {HTMLElement[]} */ ([...menu.querySelectorAll(selector)]).filter(
    (el) =>
      !el.hidden &&
      getComputedStyle(el).display !== "none" && // (inline or a stylesheet rule)
      !el.classList.contains("disabled") &&
      !el.hasAttribute("disabled"),
  );
}

/**
 * Where focus can go back to: the trigger itself if it can take focus,
 * else its nearest ancestor that can (a right-clicked label inside a row).
 * @param {EventTarget | null | undefined} trigger
 * @returns {HTMLElement | null}
 */
function _focusTarget(trigger) {
  if (!(trigger instanceof HTMLElement)) return null;
  return trigger.matches(FOCUSABLE) ? trigger : trigger.closest(FOCUSABLE);
}

/**
 * What opened ``menu`` (for a dialog opened from one of its items).
 * @param {Element | null} menu
 * @returns {HTMLElement | null}
 */
export function menuTriggerOf(menu) {
  return (menu && _lastTrigger.get(menu)) || null;
}

/**
 * @param {HTMLElement | null} menu
 * @param {{trigger?: EventTarget | null, items?: string, onEscape?: () => void}} [opts]
 *   ``onEscape``: for a menu with no Escape handling of its own — called
 *   (and the key stopped) when Escape is pressed inside it.
 */
/**
 * Run the `data-action` of a clicked item in a runtime-built menu.
 *
 * The global click dispatcher in globals.js deliberately skips anything
 * inside `.ctx-menu` (those menus own their clicks), so a menu built at
 * runtime must wire its items itself — this is that one place. Args
 * come from `data-arg0..N` with the dispatcher's coercion (numbers,
 * booleans); the handler is looked up in the action registry, then on
 * `window`.
 * @param {HTMLElement | null} menu
 * @param {{ items?: string, after?: () => void }} [opts]
 */
export function wireMenuActions(menu, opts = {}) {
  if (!menu || /** @type {any} */ (menu).__bppWired) return;
  /** @type {any} */ (menu).__bppWired = true;
  const selector = opts.items || ITEM_SELECTOR;
  menu.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement | null} */ (e.target);
    const item = /** @type {HTMLElement | null} */ (target?.closest(selector));
    if (!item || !menu.contains(item)) return;
    const name = item.dataset.action;
    if (!name) return;
    /** @type {any} */
    const win = window;
    const registered = win.__bppActionRegistry && win.__bppActionRegistry.get(name);
    const fn = registered !== undefined ? registered : win[name];
    if (typeof fn !== "function") {
      console.warn("bpp menu: unknown action:", name);
      return;
    }
    const args = [];
    for (let i = 0; item.dataset["arg" + i] !== undefined; i++) {
      args.push(_coerceArg(/** @type {string} */ (item.dataset["arg" + i])));
    }
    fn.apply(item, args);
    opts.after?.();
  });
}

/** @param {string} s */
function _coerceArg(s) {
  if (s === "true") return true;
  if (s === "false") return false;
  if (s !== "" && !isNaN(+s)) return +s;
  return s;
}

export function menuOpened(menu, opts = {}) {
  if (!menu) return;
  menuClosed(menu, { keepFocus: true });
  const selector = opts.items || ITEM_SELECTOR;
  const trigger = _focusTarget(opts.trigger);
  menu.setAttribute("role", "menu");
  const items = _liveItems(menu, selector);
  for (const el of items) {
    el.setAttribute("role", "menuitem");
    if (!el.hasAttribute("tabindex")) el.tabIndex = -1;
  }
  for (const el of menu.querySelectorAll(".ctx-menu-sep")) el.setAttribute("role", "separator");
  for (const el of menu.querySelectorAll(".ctx-header")) el.setAttribute("role", "presentation");
  if (trigger) {
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "true");
    _lastTrigger.set(menu, trigger);
  }
  /** @param {KeyboardEvent} e */
  const onKey = (e) => {
    if (e.key === "Escape" && opts.onEscape) {
      e.preventDefault();
      e.stopPropagation();
      opts.onEscape();
      return;
    }
    const live = _liveItems(menu, selector);
    if (!live.length) return;
    const at = live.indexOf(/** @type {HTMLElement} */ (document.activeElement));
    let next = -1;
    if (e.key === "ArrowDown") next = at < 0 ? 0 : (at + 1) % live.length;
    else if (e.key === "ArrowUp") next = at < 0 ? live.length - 1 : (at - 1 + live.length) % live.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = live.length - 1;
    else if ((e.key === "Enter" || e.key === " ") && at >= 0) {
      // (A native <button> item activates itself; a plain div needs the click.)
      e.stopPropagation();
      if (live[at].tagName === "BUTTON") return;
      e.preventDefault();
      live[at].click();
      return;
    } else return;
    // Handled here: the page behind (the lightbox's own arrow keys) never sees it.
    e.preventDefault();
    e.stopPropagation();
    live[next].focus({ preventScroll: true });
  };
  menu.addEventListener("keydown", onKey);
  _open.set(menu, { trigger, onKey });
  items[0]?.focus({ preventScroll: true });
}

/**
 * @param {HTMLElement | null} menu
 * @param {{keepFocus?: boolean}} [opts]  ``keepFocus``: don't move focus
 *   (reopening the same menu).
 */
export function menuClosed(menu, opts = {}) {
  if (!menu) return;
  const state = _open.get(menu);
  if (!state) return;
  _open.delete(menu);
  menu.removeEventListener("keydown", state.onKey);
  if (state.trigger) state.trigger.setAttribute("aria-expanded", "false");
  const focusInside = menu.contains(document.activeElement) || document.activeElement === document.body;
  if (!opts.keepFocus && state.trigger?.isConnected && focusInside) {
    try {
      state.trigger.focus({ preventScroll: true });
    } catch {
      /* unfocusable: leave focus where it is */
    }
  }
}
