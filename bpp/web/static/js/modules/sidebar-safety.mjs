// @ts-check
/**
 * Protection B — independent failure domains for the sidebar.
 *
 * Why this exists
 * ---------------
 * On 2026-06-02 a single backend 500 on /api/v1/faces/clusters threw
 * uncaught out of ``loadFaceClusters`` and aborted the startup
 * orchestration that was supposed to call ``renderAlbumNav`` after
 * each loader. The sidebar (Library, Albums, People, Tags, Trash —
 * five distinct sections, each backed by its own endpoint) blanked
 * entirely because ONE of them failed.
 *
 * Contract
 * --------
 * Each section loader exposes the same shape via a thin wrapper:
 *
 *   wrapSectionLoader("faces", () => loadFaceClusters())
 *
 * The wrapper:
 *   1. Awaits the loader.
 *   2. On success: clears any previous error sentinel.
 *   3. On error: stashes a sentinel object in a module-internal map
 *      (read via :func:`getSectionErrors`), logs the error, but does
 *      NOT re-throw. Callers can `await` it knowing it resolves to
 *      ``true`` on success / ``false`` on failure without
 *      exception-handling ceremony.
 *   4. Calls ``renderAlbumNav()`` unconditionally so the sidebar
 *      always re-renders with whatever data is currently available
 *      — bad sections just render their error pill (see
 *      ``renderSectionError``).
 *
 * The error sentinel is read by ``albums-render.mjs`` when building
 * the section header for each domain; a non-null sentinel inserts a
 * compact "couldn't load — retry" pill instead of the section's
 * normal content.
 */

import { esc } from "./text-format.mjs";
import { toast } from "./toast.mjs";

/**
 * @typedef {Object} SectionError
 * @property {string} message - User-visible description ("Couldn't load people")
 * @property {() => Promise<unknown>} retry - Re-invokes the original loader
 * @property {number} loggedAt - Date.now() at which the error fired (for telemetry)
 */

/**
 * Section identifier. Originally an enum of seven built-ins; widened
 * to plain string in P-07 so plugins can register their own sections
 * (e.g. a "trips" or "locations" plugin) and get the same retry-pill
 * / preserve-on-error protection the core sections have. Built-ins
 * still appear in {@link _builtinLabels} so the registry has a sane
 * default for them at startup.
 *
 * Convention: kebab-case, lowercase, ASCII. Used as a dict key and
 * appears in console logs.
 *
 * @typedef {string} SectionName
 */

/** @type {Record<string, SectionError>} */
const _errors = {};

/** Read-only snapshot of the current error map. Consumed by
 *  albums-render.mjs to decide whether to render an error pill. */
export function getSectionErrors() {
  return /** @type {Readonly<Record<string, SectionError>>} */ (_errors);
}

/** @param {SectionName} name */
export function getSectionError(name) {
  return _errors[name] || null;
}

/** @param {SectionName} name */
export function clearSectionError(name) {
  delete _errors[name];
}

/**
 * Wrap a section loader so it can't take down the sidebar. Returns a
 * Promise that resolves to ``true`` on success, ``false`` on
 * (handled) error. Never rejects.
 *
 * Re-renders the nav after both branches so the error pill or
 * recovered content appears immediately.
 *
 * @param {SectionName} name
 * @param {() => Promise<unknown>} loader
 * @param {{ silent?: boolean }} [opts] - silent: skip the toast for
 *   the user (use when the loader fires as a background refresh
 *   without an explicit user action triggering it).
 */
export async function wrapSectionLoader(name, loader, opts) {
  const win = /** @type {any} */ (window);
  try {
    await loader();
    if (_errors[name]) {
      clearSectionError(name);
      // Optimistic recovery: previous run had an error pill; we just
      // succeeded, so the nav re-render below will hide it.
    }
    // Trigger a sidebar re-render so the section reflects new data.
    try {
      win.renderAlbumNav?.();
    } catch (renderErr) {
      console.warn(`[sidebar-safety] renderAlbumNav after ${name} loader threw`, renderErr);
    }
    return true;
  } catch (err) {
    const message = `Couldn't load ${_displayName(name)}`;
    _errors[name] = {
      message,
      retry: () => wrapSectionLoader(name, loader, { silent: true }),
      loggedAt: Date.now(),
    };
    console.warn(`[sidebar-safety] ${name} loader failed:`, err);
    // User-visible toast unless explicitly silent. Tied to the
    // standing "no silent stalls" rule — sidebar gap is a stall.
    if (!opts?.silent) {
      toast(`${message}. Other sections still work; click the retry pill.`, "error", {
        action: {
          label: "Retry",
          fn: () => {
            wrapSectionLoader(name, loader, { silent: false });
          },
        },
      });
    }
    // Re-render so the error pill appears in the affected section
    // and the OTHER sections (the ones that already loaded) survive.
    try {
      win.renderAlbumNav?.();
    } catch (renderErr) {
      console.warn(`[sidebar-safety] renderAlbumNav after ${name} error threw`, renderErr);
    }
    return false;
  }
}

/**
 * Built-in section labels. Kept as a separate table from the live
 * registry so plugins can re-register on top and so we have one
 * place to maintain the core list.
 *
 * @type {Readonly<Record<string, string>>}
 */
const _builtinLabels = Object.freeze({
  albums: "Albums",
  faces: "People",
  pets: "Pets",
  tags: "Tags",
  groups: "Groups",
  deleted: "Recently Deleted",
  hidden: "Hidden",
});

/** Live label registry. Built-ins seed it at module load; plugins
 *  call {@link registerSectionLabel} to extend it. */
const _labels = /** @type {Record<string, string>} */ ({ ..._builtinLabels });

/**
 * Register (or override) the display label for a sidebar section.
 * Use this in a plugin's `setup()` so the toast / sentinel / fallback
 * pill all show the right name when the section's loader fails.
 *
 * @param {SectionName} name  - The internal kebab-case identifier.
 * @param {string} label      - The user-visible display label.
 */
export function registerSectionLabel(name, label) {
  if (!name || typeof name !== "string") return;
  _labels[name] = label;
}

/**
 * Test-only: drop any plugin-registered labels and reset to the
 * built-in defaults. Not part of the production surface — plugins
 * should never need to call this.
 */
export function _resetSectionLabelsForTests() {
  for (const k of Object.keys(_labels)) delete _labels[k];
  Object.assign(_labels, _builtinLabels);
}

/** @param {SectionName} name */
function _displayName(name) {
  return _labels[name] || name;
}

/**
 * Dispatcher entry for the "Retry" pill in the sidebar. Bound from
 * the rendered HTML via ``data-action="_retrySectionLoader"
 * data-arg0="faces"`` — runs the section's stored retry function.
 *
 * @param {SectionName} name
 */
export function _retrySectionLoader(name) {
  const err = _errors[name];
  if (!err) return;
  err.retry();
}

/**
 * Last-line-of-defense wrapper for ``renderAlbumNav`` itself. If the
 * render function throws (data shape mismatch, missing window
 * global, etc.), substitute a single visible error message instead
 * of leaving the sidebar empty. Convenience wrapper around the
 * generic ``safeRender`` below.
 *
 * @param {() => void} renderFn
 */
export function safeRenderNav(renderFn) {
  safeRender("album-list", "Sidebar", renderFn);
}

/**
 * Protection F — generalized render-failure boundary.
 *
 * Wraps a render function in a try/catch and substitutes a visible
 * "Reload" pill in the named container if the render throws. The
 * Jun-2 sidebar incident proved one render exception can blank an
 * entire surface; safeRender lets each top-level surface (sidebar,
 * grid, inspector, lightbox toolbar) opt into the same protection
 * with one line.
 *
 * Usage:
 *   safeRender("photo-grid", "Photo grid", () => _doRenderGrid(opts));
 *
 * @param {string} containerId  - DOM id of the surface's root element.
 * @param {string} label        - Human-readable name shown in the
 *                                fallback pill ("Photo grid", "Inspector").
 * @param {() => void} renderFn - The function that does the actual
 *                                rendering. May throw freely; the
 *                                wrapper handles it.
 */
export function safeRender(containerId, label, renderFn) {
  try {
    renderFn();
    return;
  } catch (err) {
    console.warn(`[safe-render] ${label} (#${containerId}) threw — rendering fallback`, err);
    const container = document.getElementById(containerId);
    if (!container) return;
    // Keep the fallback markup identical to the sidebar's so the
    // CSS rule already in place styles both consistently. Escape the
    // label — every current caller passes a hardcoded literal, but
    // the moment a dynamic name (album title, person name) flows
    // through, an un-escaped angle bracket or quote would render as
    // markup. Cheap to fix now; expensive to chase later.
    container.innerHTML =
      `<div class="sidebar-section-error">
        <span>${esc(label)} couldn't render.</span>
        <button class="sidebar-section-error-retry" data-action="_safeRenderReload">Reload</button>
      </div>`;
  }
}

/** Dispatcher entry for the "Reload" button in a safeRender fallback.
 *  Reloads the page — the simplest reset that gets the user past a
 *  render-time JS exception. */
export function _safeRenderReload() {
  window.location.reload();
}
