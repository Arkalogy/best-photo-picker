// @ts-check
/**
 * Fast tooltip system.
 *
 * Replaces slow browser title-attribute tooltips (~500ms native delay)
 * with instant ones (~100ms). Works via event delegation on `document`
 * — no changes needed to existing markup.
 *
 * The classic file was an IIFE that ran once on script load. As a
 * module the same effect happens once on import, attached via the
 * `<script type="module">` bootstrap in index.html. No exports are
 * needed for runtime — the listeners do all the work — but `_state`
 * and the helpers below are exported for unit testing.
 */

const DELAY = 100;

/**
 * @typedef {Object} TooltipState
 * @property {HTMLElement | null} tip
 * @property {ReturnType<typeof setTimeout> | null} timer
 * @property {(HTMLElement & { _savedTitle?: string }) | null} current
 */

/** @type {TooltipState} */
const _state = { tip: null, timer: null, current: null };

/** @returns {HTMLElement} */
function getTipEl() {
  if (!_state.tip) {
    const tip = document.createElement("div");
    tip.className = "app-tooltip";
    document.body.appendChild(tip);
    _state.tip = tip;
  }
  return _state.tip;
}

/**
 * Position the tooltip slightly below and right of the cursor; flip
 * to the opposite side when it would overflow the viewport edge.
 *
 * @param {string} text
 * @param {number} x
 * @param {number} y
 */
function show(text, x, y) {
  const tip = getTipEl();
  tip.textContent = text;
  tip.style.display = "block";
  const pad = 8;
  let left = x + 12;
  let top = y + 16;
  const rect = tip.getBoundingClientRect();
  if (left + rect.width > window.innerWidth - pad) left = x - rect.width - 4;
  if (top + rect.height > window.innerHeight - pad) top = y - rect.height - 4;
  tip.style.left = Math.max(pad, left) + "px";
  tip.style.top = Math.max(pad, top) + "px";
  requestAnimationFrame(() => tip.classList.add("visible"));
}

function hide() {
  if (_state.timer) {
    clearTimeout(_state.timer);
    _state.timer = null;
  }
  if (_state.current) {
    if (_state.current._savedTitle) {
      _state.current.setAttribute("title", _state.current._savedTitle);
      delete _state.current._savedTitle;
    }
    _state.current = null;
  }
  if (_state.tip) {
    _state.tip.classList.remove("visible");
    _state.tip.style.display = "none";
  }
}

document.addEventListener("mouseover", (e) => {
  const target = /** @type {Element} */ (e.target);
  const el = /** @type {HTMLElement | null} */ (target.closest?.("[title]"));
  if (!el || !el.title) return;
  if (_state.current === el) return;

  hide();
  const text = el.title;
  // Suppress native tooltip by stashing title and removing the attribute.
  /** @type {any} */ (el)._savedTitle = text;
  el.removeAttribute("title");
  _state.current = el;
  _state.timer = setTimeout(() => show(text, e.clientX, e.clientY), DELAY);
});

document.addEventListener("mouseout", (e) => {
  const target = /** @type {Element} */ (e.target);
  const el =
    /** @type {HTMLElement | null} */ (target.closest?.("[title]")) || _state.current;
  if (!el || el !== _state.current) return;
  // Don't hide while moving between descendants of the current target.
  const related = /** @type {Element | null} */ (e.relatedTarget);
  if (related && el.contains(related)) return;
  hide();
});

// Defensive: any explicit user action should dismiss the tooltip.
document.addEventListener("scroll", hide, true);
document.addEventListener("click", hide, true);
document.addEventListener("keydown", hide, true);

// Test-only exports — runtime callers don't import these.
export { _state, getTipEl, hide, show };
